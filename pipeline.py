#!/usr/bin/env python3
"""
Instagram Saves -> Obsidian Vault pipeline.

Runs on the user's own machine (home IP, real session) for account safety:
  1. Fetch the account's *saved* feed via Instaloader using a REUSED session
     file (never logs in with a password, never retries logins).
  2. Download only NEW video posts (dedup via state.json), capped per run,
     with jittered delays between requests.
  3. Extract audio (bundled ffmpeg) and transcribe locally with faster-whisper.
  4. Extract keyframes so the review step can "see" the video.
  5. Hand each item to the "brain" (Claude) which verifies the transcript,
     categorizes, and writes a proper Obsidian note into the vault.

Usage:
  python pipeline.py                  # normal scheduled run
  python pipeline.py --limit 2        # cap downloads this run
  python pipeline.py --no-download    # only process what's already in inbox
  python pipeline.py --backfill 40    # scan deeper into older saves
  python pipeline.py --reprocess SC   # re-run brain for shortcode SC
  python pipeline.py --dry-run        # scan and report, download nothing
"""

import argparse
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LOCK_FILE = SCRIPT_DIR / "pipeline.lock"
STATE_FILE = SCRIPT_DIR / "state.json"
CONFIG_FILE = SCRIPT_DIR / "config.json"
CONFIG_DEFAULT = SCRIPT_DIR / "config.default.json"

log = logging.getLogger("igpipeline")


# --------------------------------------------------------------------------- #
# Config / state
# --------------------------------------------------------------------------- #

def load_config() -> dict:
    with open(CONFIG_DEFAULT, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    if cfg.get("vault_path") in (None, "", "auto"):
        # Installed at <vault>/_automation/instagram-pipeline/
        cfg["vault_path"] = str(SCRIPT_DIR.parent.parent)
    return cfg


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"initialized": False, "posts": {}, "runs": []}


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    tmp.replace(STATE_FILE)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Vault helpers
# --------------------------------------------------------------------------- #

def vault_paths(cfg: dict) -> dict:
    vault = Path(cfg["vault_path"])
    notes = vault / cfg["notes_folder"]
    return {
        "vault": vault,
        "notes": notes,
        "inbox": SCRIPT_DIR / "inbox",
        "media": SCRIPT_DIR / "media",
        "logs": SCRIPT_DIR / "logs",
        "index": notes / "_Index.md",
        "attention": notes / "_ATTENTION Instagram Pipeline.md",
        "runlog": SCRIPT_DIR / "RUNLOG.md",
    }


def write_attention(paths: dict, message: str) -> None:
    paths["notes"].mkdir(parents=True, exist_ok=True)
    body = (
        "---\ntags:\n  - instagram/pipeline\n---\n\n"
        "> [!danger] Instagram pipeline needs attention\n"
        f"> {message}\n>\n"
        f"> _Written {now_iso()}. This note is deleted automatically once the "
        "problem is resolved._\n"
    )
    paths["attention"].write_text(body, encoding="utf-8")
    log.warning("ATTENTION note written: %s", message)


def clear_attention(paths: dict) -> None:
    if paths["attention"].exists():
        paths["attention"].unlink()


def append_runlog(paths: dict, line: str) -> None:
    f = paths["runlog"]
    if not f.exists():
        f.write_text("# Pipeline run log\n\n", encoding="utf-8")
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(line.rstrip() + "\n")


# --------------------------------------------------------------------------- #
# Locking
# --------------------------------------------------------------------------- #

def _pid_alive(pid: int) -> bool:
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                                 capture_output=True, text=True, timeout=15)
            return str(pid) in out.stdout
        os.kill(pid, 0)
        return True
    except Exception:  # noqa: BLE001
        return False


def acquire_lock() -> bool:
    if LOCK_FILE.exists():
        stale = False
        try:
            pid = int(LOCK_FILE.read_text(encoding="utf-8").strip() or 0)
            if not pid or not _pid_alive(pid):
                stale = True  # owner died (e.g. console window closed)
            elif time.time() - LOCK_FILE.stat().st_mtime > 3 * 3600:
                stale = True
        except (OSError, ValueError):
            stale = True
        if not stale:
            return False
        log.warning("Stale lock (owner gone) - removing.")
        LOCK_FILE.unlink(missing_ok=True)
    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release_lock() -> None:
    LOCK_FILE.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Instagram (Instaloader)
# --------------------------------------------------------------------------- #

def get_loader(cfg: dict, paths: dict):
    import instaloader

    L = instaloader.Instaloader(
        download_pictures=False,
        download_videos=True,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        post_metadata_txt_pattern="",
        dirname_pattern=str(paths["media"] / "{target}"),
        filename_pattern="{shortcode}",
        quiet=True,
        max_connection_attempts=1,  # never hammer on errors
    )
    username = cfg.get("instagram_username") or ""
    session_file = SCRIPT_DIR / f"session-{username}"
    if not username or not session_file.exists():
        raise RuntimeError(
            "No Instagram session found. Run:  python import_session.py"
        )
    L.load_session_from_file(username, str(session_file))
    return L


def jitter_sleep(cfg: dict) -> None:
    s = random.uniform(cfg["min_delay_seconds"], cfg["max_delay_seconds"])
    log.info("  waiting %.0fs (polite delay) ...", s)
    time.sleep(s)


def scan_saved_feed(L, cfg: dict, state: dict, args) -> list:
    """Return list of NEW candidate posts (newest first), politely capped."""
    import instaloader

    profile = instaloader.Profile.own_profile(L.context)
    known = state["posts"]

    if args.backfill:
        scan_cap, streak_cap = args.backfill, args.backfill + 1
    elif not state.get("initialized"):
        scan_cap, streak_cap = cfg["initial_backfill_limit"], 10**9
    else:
        scan_cap, streak_cap = cfg["scan_limit"], cfg["stop_after_known_streak"]

    candidates, streak, scanned = [], 0, 0
    for post in profile.get_saved_posts():
        scanned += 1
        if post.shortcode in known:
            streak += 1
            if streak >= streak_cap:
                log.info("Hit %d known posts in a row - stopping scan.", streak)
                break
        else:
            streak = 0
            candidates.append(post)
        if scanned >= scan_cap:
            log.info("Scan cap (%d) reached.", scan_cap)
            break
    log.info("Scanned %d saved posts, %d new candidates.", scanned, len(candidates))
    return candidates


def post_is_video(post) -> bool:
    """True if post contains at least one video. May cost one metadata fetch
    for sidecar (album) posts - acceptable for the few candidates per run."""
    try:
        if post.typename == "GraphSidecar":
            return any(n.is_video for n in post.get_sidecar_nodes())
        return bool(post.is_video)
    except Exception as e:  # noqa: BLE001
        # Instagram sometimes refuses the metadata query (GraphQL flakiness).
        # Be optimistic: treat it as a video candidate. If it turns out to be
        # an image post, no mp4 gets downloaded and the item ends terminal as
        # failed:missing_media instead of a real video being lost forever.
        log.warning("Could not determine media type for %s (%s) - treating "
                    "as video candidate.", post.shortcode, e)
        return True


def download_new_posts(cfg: dict, paths: dict, state: dict, args) -> int:
    """Scan the saved feed and download new videos. Returns count downloaded."""
    import instaloader

    try:
        L = get_loader(cfg, paths)
    except RuntimeError as e:
        write_attention(paths, str(e))
        return 0

    limit = args.limit if args.limit is not None else cfg["max_downloads_per_run"]

    try:
        candidates = scan_saved_feed(L, cfg, state, args)
    except instaloader.exceptions.LoginRequiredException:
        write_attention(
            paths,
            "Instagram session expired. Re-run `python import_session.py` in the "
            "pipeline folder to refresh it (no password is ever stored).",
        )
        return 0
    except instaloader.exceptions.TooManyRequestsException:
        log.error("Rate limited by Instagram (429). Aborting this run entirely.")
        append_runlog(paths, f"- {now_iso()} rate-limited (429); run aborted early")
        return 0

    clear_attention(paths)

    downloaded = 0
    for post in candidates:
        if args.dry_run:
            log.info("[dry-run] would inspect %s", post.shortcode)
            continue
        if downloaded >= limit:
            log.info("Per-run download cap (%d) reached.", limit)
            break

        sc = post.shortcode
        try:
            if not post_is_video(post):
                state["posts"][sc] = {"status": "skipped_nonvideo",
                                      "added": now_iso()}
                save_state(state)
                continue

            log.info("Downloading %s ...", sc)
            target_dir = paths["media"] / sc
            target_dir.mkdir(parents=True, exist_ok=True)
            L.download_post(post, target=sc)

            meta = {
                "shortcode": sc,
                "url": f"https://www.instagram.com/p/{sc}/",
                "author": _safe(lambda: post.owner_username, "unknown"),
                "caption": _safe(lambda: post.caption, "") or "",
                "posted": _safe(lambda: post.date_utc.strftime("%Y-%m-%d"), ""),
                "detected": now_iso(),
            }
            (target_dir / "meta.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

            state["posts"][sc] = {"status": "downloaded", "added": now_iso()}
            save_state(state)
            downloaded += 1
            log.info("  ok (%d/%d)", downloaded, limit)
            jitter_sleep(cfg)

        except instaloader.exceptions.TooManyRequestsException:
            log.error("Rate limited mid-run. Stopping downloads immediately.")
            append_runlog(paths, f"- {now_iso()} rate-limited mid-run after "
                                 f"{downloaded} downloads")
            break
        except Exception as e:  # noqa: BLE001
            log.error("Download failed for %s: %s", sc, e)
            state["posts"][sc] = {"status": "failed:download", "added": now_iso(),
                                  "error": str(e)[:300]}
            save_state(state)
            jitter_sleep(cfg)

    if not state.get("initialized") and not args.dry_run:
        state["initialized"] = True
        save_state(state)
    return downloaded


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


# --------------------------------------------------------------------------- #
# Media: audio extraction, transcription, keyframes
# --------------------------------------------------------------------------- #

def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        exe = shutil.which("ffmpeg")
        if exe:
            return exe
        raise RuntimeError("ffmpeg not available (pip install imageio-ffmpeg)")


def run_ffmpeg(args_list: list) -> None:
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
                    *args_list], check=True, timeout=600)


def extract_audio(video: Path, wav: Path) -> bool:
    try:
        run_ffmpeg(["-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                    str(wav)])
        return wav.exists() and wav.stat().st_size > 1000
    except Exception as e:  # noqa: BLE001
        log.warning("Audio extraction failed for %s: %s", video.name, e)
        return False


_WHISPER_MODEL = None


def _add_nvidia_dll_dirs() -> None:
    """If CUDA runtime wheels (nvidia-cublas-cu12, nvidia-cudnn-cu12) are
    installed in this venv, make their DLLs loadable on Windows so the GPU
    path works. No-op otherwise."""
    if os.name != "nt":
        return
    try:
        import site
        for sp in site.getsitepackages():
            base = Path(sp) / "nvidia"
            if base.exists():
                for sub in list(base.glob("*/bin")) + list(base.glob("*/lib")):
                    try:
                        os.add_dll_directory(str(sub))
                    except OSError:
                        pass
    except Exception:  # noqa: BLE001
        pass


def get_whisper(cfg: dict):
    global _WHISPER_MODEL
    if _WHISPER_MODEL is not None:
        return _WHISPER_MODEL
    if os.environ.get("IGV_FAKE_WHISPER"):
        _WHISPER_MODEL = "FAKE"
        return _WHISPER_MODEL
    from faster_whisper import WhisperModel
    name = cfg["whisper_model"]
    device = cfg.get("whisper_device", "auto")
    if device in ("auto", "cuda"):
        try:
            _add_nvidia_dll_dirs()
            _WHISPER_MODEL = WhisperModel(name, device="cuda",
                                          compute_type="float16")
            log.info("Whisper '%s' on GPU.", name)
            return _WHISPER_MODEL
        except Exception:  # noqa: BLE001
            if device == "cuda":
                raise
            log.info("GPU unavailable, using CPU.")
    _WHISPER_MODEL = WhisperModel(name, device="cpu", compute_type="int8")
    log.info("Whisper '%s' on CPU (int8).", name)
    return _WHISPER_MODEL


def _force_cpu_whisper(cfg: dict):
    """Rebuild the model on CPU after a GPU runtime failure (missing
    cuBLAS/cuDNN DLLs surface at transcribe time, not at model load)."""
    global _WHISPER_MODEL
    from faster_whisper import WhisperModel
    _WHISPER_MODEL = WhisperModel(cfg["whisper_model"], device="cpu",
                                  compute_type="int8")
    log.warning("GPU transcription unavailable - switched to CPU (int8). "
                "To enable GPU:  venv\\Scripts\\pip install "
                "nvidia-cublas-cu12 nvidia-cudnn-cu12")
    return _WHISPER_MODEL


def _run_transcribe(model, cfg: dict, wav: Path) -> tuple:
    segments, info = model.transcribe(
        str(wav), language=cfg.get("language"), vad_filter=True, beam_size=5)
    segs, parts = [], []
    for s in segments:
        segs.append({"start": round(s.start, 2), "end": round(s.end, 2),
                     "text": s.text.strip()})
        parts.append(s.text.strip())
    return (" ".join(parts).strip(), segs,
            float(getattr(info, "duration", 0) or 0),
            getattr(info, "language", None))


def transcribe(cfg: dict, wav: Path) -> tuple:
    """Returns (plain_text, segments_list, duration_seconds, language)."""
    model = get_whisper(cfg)
    if model == "FAKE":
        return ("[fake transcript for testing]",
                [{"start": 0.0, "end": 2.0, "text": "[fake transcript]"}],
                10.0, "en")
    try:
        return _run_transcribe(model, cfg, wav)
    except (RuntimeError, OSError) as e:
        msg = str(e).lower()
        if any(k in msg for k in ("cublas", "cudnn", "cuda", "cudart")):
            return _run_transcribe(_force_cpu_whisper(cfg), cfg, wav)
        raise


def extract_frames(video: Path, out_dir: Path, duration: float,
                   shortcode: str, count: int = 5) -> list:
    """Evenly spaced frames across the video (claude-video style sampling),
    so the review step sees the whole arc, not just three moments."""
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    dur = max(duration, 1.0)
    count = max(1, min(int(count), 12))
    fracs = [(i + 0.5) / count for i in range(count)]
    for i, frac in enumerate(fracs, 1):
        out = out_dir / f"{shortcode}_frame{i}.jpg"
        try:
            run_ffmpeg(["-ss", f"{dur * frac:.2f}", "-i", str(video),
                        "-frames:v", "1", "-vf", "scale='min(640,iw)':-2",
                        str(out)])
            if out.exists():
                frames.append(out)
        except Exception as e:  # noqa: BLE001
            log.warning("Frame %d failed for %s: %s", i, shortcode, e)
    return frames


def process_pending(cfg: dict, paths: dict, state: dict, args) -> tuple:
    """Transcribe + brain every item not yet fully processed.
    Returns (processed_count, failed_count)."""
    import brain

    pending = [sc for sc, rec in state["posts"].items()
               if rec.get("status") in ("downloaded", "transcribed",
                                        "failed:transcribe", "failed:brain")]
    if args.reprocess:
        pending = [args.reprocess]
        if args.reprocess in state["posts"]:
            state["posts"][args.reprocess]["status"] = "transcribed"

    ok = failed = 0
    for sc in pending:
        rec = state["posts"].get(sc, {})
        media_dir = paths["media"] / sc
        inbox_dir = paths["inbox"] / sc
        inbox_dir.mkdir(parents=True, exist_ok=True)
        videos = sorted(media_dir.glob("*.mp4"))
        if not videos:
            log.warning("No video files for %s - marking failed.", sc)
            rec["status"] = "failed:missing_media"
            state["posts"][sc] = rec
            save_state(state)
            failed += 1
            continue

        try:
            # -- transcription ------------------------------------------------
            if not (inbox_dir / "transcript.txt").exists() or \
                    rec.get("status") in ("downloaded", "failed:transcribe"):
                all_text, all_segs, langs, total_dur = [], [], [], 0.0
                for vi, video in enumerate(videos, 1):
                    wav = inbox_dir / f"{sc}_{vi}.wav"
                    if not extract_audio(video, wav):
                        all_text.append("[no audio track]")
                        continue
                    text, segs, dur, lang = transcribe(cfg, wav)
                    if dur / 60.0 > cfg["max_video_minutes"]:
                        log.warning("%s part %d is %.0f min - beyond cap.",
                                    sc, vi, dur / 60)
                    prefix = f"[video {vi}] " if len(videos) > 1 else ""
                    all_text.append(prefix + (text or "[no speech detected]"))
                    all_segs.extend(segs)
                    langs.append(lang)
                    total_dur += dur
                    wav.unlink(missing_ok=True)

                (inbox_dir / "transcript.txt").write_text(
                    "\n\n".join(all_text), encoding="utf-8")
                (inbox_dir / "segments.json").write_text(
                    json.dumps(all_segs, indent=1, ensure_ascii=False),
                    encoding="utf-8")

                meta_file = media_dir / "meta.json"
                meta = json.loads(meta_file.read_text(encoding="utf-8")) \
                    if meta_file.exists() else {"shortcode": sc}
                meta["duration_seconds"] = round(total_dur, 1)
                meta["language"] = next((l for l in langs if l), None)
                meta["video_files"] = [str(v) for v in videos]
                (inbox_dir / "meta.json").write_text(
                    json.dumps(meta, indent=2, ensure_ascii=False),
                    encoding="utf-8")

                extract_frames(videos[0], inbox_dir / "frames", total_dur, sc,
                               cfg.get("frame_count", 5))
                rec["status"] = "transcribed"
                state["posts"][sc] = rec
                save_state(state)
                log.info("Transcribed %s (%.0fs, lang=%s).",
                         sc, total_dur, meta.get("language"))

            # -- brain (Claude review, categorize, write note) ----------------
            result = brain.process_item(cfg, paths, sc)
            rec["status"] = "processed"
            rec["note_path"] = result.get("note_path", "")
            rec["category"] = result.get("category", "")
            rec["processed"] = now_iso()
            rec["reviewed_by"] = result.get("mode", "")
            state["posts"][sc] = rec
            save_state(state)
            ok += 1
            log.info("Processed %s -> %s", sc, rec["note_path"])

            if not cfg.get("keep_videos", True):
                for v in videos:
                    v.unlink(missing_ok=True)

        except brain.BrainError as e:
            log.error("Brain failed for %s: %s", sc, e)
            rec["status"] = "failed:brain"
            rec["error"] = str(e)[:300]
            state["posts"][sc] = rec
            save_state(state)
            failed += 1
        except Exception as e:  # noqa: BLE001
            log.error("Processing failed for %s: %s", sc, e, exc_info=args.verbose)
            rec["status"] = "failed:transcribe"
            rec["error"] = str(e)[:300]
            state["posts"][sc] = rec
            save_state(state)
            failed += 1

    return ok, failed


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def setup_logging(paths: dict, verbose: bool) -> None:
    paths["logs"].mkdir(parents=True, exist_ok=True)
    logfile = paths["logs"] / f"pipeline-{datetime.now():%Y%m}.log"
    handlers = [logging.FileHandler(logfile, encoding="utf-8"),
                logging.StreamHandler(sys.stdout)]
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers)
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)


def main() -> int:
    p = argparse.ArgumentParser(description="Instagram Saves -> Obsidian vault")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--backfill", type=int, default=None)
    p.add_argument("--no-download", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--reprocess", type=str, default=None)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    cfg = load_config()
    paths = vault_paths(cfg)
    for k in ("inbox", "media", "logs"):
        paths[k].mkdir(parents=True, exist_ok=True)
    setup_logging(paths, args.verbose)

    if not acquire_lock():
        log.error("Another run is in progress (pipeline.lock). Exiting.")
        return 3

    started = now_iso()
    log.info("=== Run started %s (vault: %s) ===", started, cfg["vault_path"])
    dl = ok = failed = 0
    try:
        state = load_state()
        if not args.no_download and not args.reprocess:
            dl = download_new_posts(cfg, paths, state, args)
        if not args.dry_run:
            ok, failed = process_pending(cfg, paths, state, args)
        state.setdefault("runs", []).append(
            {"at": started, "downloaded": dl, "processed": ok, "failed": failed})
        state["runs"] = state["runs"][-60:]
        save_state(state)
        append_runlog(paths, f"- {started} downloaded={dl} processed={ok} "
                             f"failed={failed}")
        log.info("=== Run done: %d downloaded, %d processed, %d failed ===",
                 dl, ok, failed)
        return 0 if failed == 0 else 1
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
