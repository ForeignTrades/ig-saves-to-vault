#!/usr/bin/env python3
"""
The "brain" step: Claude verifies the Whisper transcript, categorizes the
video, and writes a proper Obsidian note into the vault.

Three modes (config "brain": "auto" | "claude-cli" | "api" | "basic"):

  claude-cli  Headless Claude Code on the user's machine (uses their Claude
              subscription). Claude reads the transcript + keyframes itself,
              picks/creates a category, writes the note, updates the index.
  api         Direct Anthropic API call (needs ANTHROPIC_API_KEY). Returns
              structured JSON; this module writes the note deterministically.
  basic       No AI available: files an "unreviewed" note with the raw
              transcript so nothing is ever lost; can be reprocessed later
              with --reprocess once a brain is available.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROMPT_FILE = SCRIPT_DIR / "brain_prompt.md"


class BrainError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def detect_mode(cfg: dict) -> str:
    mode = cfg.get("brain", "auto")
    if mode != "auto":
        return mode
    if shutil.which("claude"):
        return "claude-cli"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    return "basic"


def existing_categories(paths: dict) -> list:
    notes = paths["notes"]
    if not notes.exists():
        return []
    return sorted(d.name for d in notes.iterdir()
                  if d.is_dir() and not d.name.startswith(("_", ".")))


def safe_filename(s: str, maxlen: int = 60) -> str:
    s = re.sub(r'[<>:"/\\|?*#^\[\]\n\r\t]', "", s).strip().rstrip(".")
    return (s[:maxlen].strip() or "untitled")


def load_item(paths: dict, sc: str) -> dict:
    inbox = paths["inbox"] / sc
    meta = json.loads((inbox / "meta.json").read_text(encoding="utf-8"))
    transcript = (inbox / "transcript.txt").read_text(encoding="utf-8")
    frames = sorted((inbox / "frames").glob("*.jpg")) \
        if (inbox / "frames").exists() else []
    return {"inbox": inbox, "meta": meta, "transcript": transcript,
            "frames": frames}


def append_index(paths: dict, note_path: Path, title: str, author: str,
                 category: str, status: str) -> None:
    idx = paths["index"]
    if not idx.exists():
        idx.parent.mkdir(parents=True, exist_ok=True)
        idx.write_text(
            "---\ntags:\n  - instagram/index\n---\n\n"
            "# Instagram saves index\n\n"
            "| Date | Note | Author | Category | Status |\n"
            "| --- | --- | --- | --- | --- |\n",
            encoding="utf-8")
    link = f"[[{note_path.stem}]]"
    row = (f"| {date.today().isoformat()} | {link} | @{author} "
           f"| {category} | {status} |\n")
    with open(idx, "a", encoding="utf-8") as f:
        f.write(row)


def write_note(paths: dict, sc: str, meta: dict, category: str, title: str,
               summary: str, key_points: list, tags: list, transcript: str,
               status: str, frames: list, vault_videos: list = None) -> Path:
    folder = paths["notes"] / category
    folder.mkdir(parents=True, exist_ok=True)
    fname = f"{date.today().isoformat()} {safe_filename(title)}.md"
    note = folder / fname
    n = 1
    while note.exists():
        n += 1
        note = folder / f"{date.today().isoformat()} {safe_filename(title)} {n}.md"

    tag_lines = "\n".join(f"  - {t}" for t in
                          ["instagram/save"] + [t.strip("#") for t in tags])
    points = "\n".join(f"- {p}" for p in key_points) if key_points else ""
    if vault_videos:
        frame_embed = f"![[{vault_videos[0]}]]\n\n"
    elif frames:
        frame_embed = f"![[{frames[0].name}|400]]\n\n"
    else:
        frame_embed = ""
    caption = (meta.get("caption") or "").strip()
    caption_block = ""
    if caption:
        short = caption if len(caption) < 600 else caption[:600] + " [...]"
        quoted = "\n".join("> " + ln for ln in short.splitlines())
        caption_block = f"\n> [!quote]- Original caption\n{quoted}\n"

    body = f"""---
title: "{title.replace('"', "'")}"
source: instagram
author: "{meta.get('author', 'unknown')}"
url: {meta.get('url', '')}
posted: {meta.get('posted', '')}
processed: {date.today().isoformat()}
category: "{category}"
duration_seconds: {meta.get('duration_seconds', '')}
language: {meta.get('language', '')}
status: {status}
tags:
{tag_lines}
---

# {title}

> [!abstract] Summary
> {summary}

{points}

**Author:** @{meta.get('author', 'unknown')} · **Posted:** {meta.get('posted', '?')} · [Watch on Instagram]({meta.get('url', '')})

{frame_embed}{caption_block}
## Transcript

{transcript.strip()}
"""
    note.write_text(body, encoding="utf-8")
    append_index(paths, note, title, meta.get("author", "unknown"),
                 category, status)
    return note


# --------------------------------------------------------------------------- #
# Mode: claude-cli (headless Claude Code)
# --------------------------------------------------------------------------- #

def run_claude_cli(cfg: dict, paths: dict, sc: str, item: dict) -> dict:
    template = PROMPT_FILE.read_text(encoding="utf-8")
    vault = paths["vault"]

    def rel(p):
        # The pipeline (and its inbox) may live OUTSIDE the vault; fall back
        # to an absolute path in that case.
        p = Path(p).resolve()
        try:
            return str(p.relative_to(vault)).replace("\\", "/")
        except ValueError:
            return str(p).replace("\\", "/")

    cats = existing_categories(paths)
    video_embed = (f"![[{item['vault_videos'][0]}]]"
                   if item.get("vault_videos") else "")
    prompt = (template
              .replace("{SHORTCODE}", sc)
              .replace("{INBOX_DIR}", rel(item["inbox"]))
              .replace("{NOTES_FOLDER}", cfg["notes_folder"])
              .replace("{EXISTING_CATEGORIES}",
                       ", ".join(cats) if cats else "(none yet)")
              .replace("{URL}", item["meta"].get("url", ""))
              .replace("{VIDEO_EMBED}", video_embed)
              .replace("{TODAY}", date.today().isoformat()))

    exe = shutil.which("claude")
    if not exe:
        raise BrainError("claude CLI not found on PATH")
    cmd = [exe, "-p", prompt,
           "--allowedTools", "Read,Write,Edit,Glob,Grep",
           "--add-dir", str(SCRIPT_DIR),
           "--output-format", "text"]
    if exe.lower().endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c", *cmd]

    try:
        proc = subprocess.run(
            cmd, cwd=str(vault), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=cfg.get("brain_timeout_seconds", 420))
    except subprocess.TimeoutExpired:
        raise BrainError("claude CLI timed out")
    if proc.returncode != 0:
        raise BrainError(f"claude CLI exit {proc.returncode}: "
                         f"{(proc.stderr or proc.stdout or '')[:400]}")

    result_file = item["inbox"] / "result.json"
    if not result_file.exists():
        raise BrainError("claude CLI finished but wrote no result.json")
    result = json.loads(result_file.read_text(encoding="utf-8"))
    note_path = vault / result.get("note_path", "")
    if not result.get("note_path") or not note_path.exists():
        raise BrainError(f"note file missing: {result.get('note_path')}")
    result["mode"] = "claude-cli"
    return result


# --------------------------------------------------------------------------- #
# Mode: api (direct Anthropic API)
# --------------------------------------------------------------------------- #

API_SYSTEM = """You review transcripts of Instagram videos for a personal \
knowledge vault. You receive a Whisper transcript (may contain recognition \
errors), the post caption/metadata, and up to 3 keyframes. Respond ONLY with \
JSON: {"title": str (<=8 words), "summary": str (2-3 sentences), \
"key_points": [str, ...] (2-6 bullets), "category": str (Title Case, single \
term; prefer one of the existing categories given), "tags": [str, ...] \
(3-6 lowercase), "clean_transcript": str (the transcript with obvious \
speech-recognition errors fixed using context; do NOT invent or add \
content; keep original language and meaning)}."""


def run_api(cfg: dict, paths: dict, sc: str, item: dict) -> dict:
    import requests

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise BrainError("ANTHROPIC_API_KEY not set")

    content = []
    for fp in item["frames"][:3]:
        b64 = base64.standard_b64encode(fp.read_bytes()).decode()
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": b64}})
    cats = existing_categories(paths)
    content.append({"type": "text", "text": json.dumps({
        "metadata": item["meta"],
        "existing_categories": cats,
        "transcript": item["transcript"][:24000],
    }, ensure_ascii=False)})

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": cfg.get("claude_model", "claude-sonnet-4-5"),
              "max_tokens": 8000, "system": API_SYSTEM,
              "messages": [{"role": "user", "content": content}]},
        timeout=cfg.get("brain_timeout_seconds", 420))
    if resp.status_code != 200:
        raise BrainError(f"API {resp.status_code}: {resp.text[:300]}")
    text = "".join(b.get("text", "") for b in resp.json().get("content", []))
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise BrainError("API response contained no JSON")
    data = json.loads(m.group(0))

    note = write_note(
        paths, sc, item["meta"],
        category=data.get("category") or "Uncategorized",
        title=data.get("title") or sc,
        summary=data.get("summary", ""),
        key_points=data.get("key_points", []),
        tags=data.get("tags", []),
        transcript=data.get("clean_transcript") or item["transcript"],
        status="reviewed", frames=item["frames"],
        vault_videos=item.get("vault_videos"))
    return {"note_path": str(note.relative_to(paths["vault"])),
            "category": data.get("category", "Uncategorized"),
            "title": data.get("title", sc), "mode": "api"}


# --------------------------------------------------------------------------- #
# Mode: basic (no AI available - never lose content)
# --------------------------------------------------------------------------- #

def run_basic(cfg: dict, paths: dict, sc: str, item: dict) -> dict:
    caption = (item["meta"].get("caption") or "").strip()
    title = " ".join(caption.split()[:7]) if caption else f"Instagram save {sc}"
    note = write_note(
        paths, sc, item["meta"], category="_Unreviewed", title=title,
        summary="Not yet reviewed by Claude - raw Whisper transcript below. "
                f"Reprocess with: python pipeline.py --reprocess {sc}",
        key_points=[], tags=["unreviewed"],
        transcript=item["transcript"], status="unreviewed",
        frames=item["frames"], vault_videos=item.get("vault_videos"))
    return {"note_path": str(note.relative_to(paths["vault"])),
            "category": "_Unreviewed", "title": title, "mode": "basic"}


# --------------------------------------------------------------------------- #

def process_item(cfg: dict, paths: dict, sc: str) -> dict:
    try:
        return _process_item(cfg, paths, sc)
    except BrainError:
        raise
    except Exception as e:  # noqa: BLE001  - label brain failures correctly
        raise BrainError(f"{type(e).__name__}: {e}")


def _process_item(cfg: dict, paths: dict, sc: str) -> dict:
    item = load_item(paths, sc)
    attach = paths["notes"] / "_attachments"
    attach.mkdir(parents=True, exist_ok=True)

    # Copy first keyframe into the vault so notes can embed it.
    for fp in item["frames"][:1]:
        dest = attach / fp.name
        if not dest.exists():
            shutil.copy2(fp, dest)

    # Copy the video itself into the vault so notes get a playable embed.
    item["vault_videos"] = []
    if cfg.get("copy_video_to_vault", True):
        media_videos = sorted((paths["media"] / sc).glob("*.mp4"))
        for i, v in enumerate(media_videos):
            name = f"{sc}.mp4" if i == 0 else f"{sc}_{i + 1}.mp4"
            dest = attach / name
            if not dest.exists():
                shutil.copy2(v, dest)
            item["vault_videos"].append(name)

    mode = detect_mode(cfg)
    if mode == "claude-cli":
        try:
            return run_claude_cli(cfg, paths, sc, item)
        except BrainError as e:
            print(f"claude-cli brain failed ({e}); falling back.",
                  file=sys.stderr)
            if os.environ.get("ANTHROPIC_API_KEY"):
                return run_api(cfg, paths, sc, item)
            raise
    if mode == "api":
        return run_api(cfg, paths, sc, item)
    return run_basic(cfg, paths, sc, item)
