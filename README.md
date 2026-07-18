# IG Saves → Vault

**Turn your Instagram saved videos into a searchable, AI-curated knowledge vault — automatically, every day, without risking your account.**

You save videos on Instagram and never look at them again. This pipeline fixes that: once a day it quietly picks up your *new* saved videos, transcribes them locally with Whisper, has Claude verify the transcript, research the topic, and categorize it — then files a clean, structured Obsidian note with the video embedded and the corrected transcript underneath. Everything is also written to a machine-readable index, so your own AI agents can query the collection directly.

```
Instagram saves ──► download (your PC, your session) ──► Whisper transcription (local)
                                                              │
        Obsidian note + playable video + JSON index ◄── Claude review, research
                                                        & categorization
```

## Why it won't get your account banned (as far as any tool can promise)

Most Instagram scrapers get accounts flagged because they log in with passwords from scripts, run from datacenter IPs, and hammer the API. This pipeline is designed around the opposite:

- Runs on **your own machine, from your home IP**, reusing the **session your browser already has** — never a scripted password login, ever.
- **One** saved-feed check per day at a **randomized** time (Task Scheduler adds up to 45 min of jitter).
- Downloads are capped per run (default 8) with 25–70 s randomized pauses between posts; feed scanning stops early as soon as it hits posts it already knows.
- On any rate-limit response (429), the run **aborts instantly** — it never retries or hammers.
- If the session expires, it writes an `_ATTENTION` note into your vault and stops, rather than guessing at logins.

No automation is zero-risk. Don't run other Instagram bots on the same account, and keep the caps sensible.

## What lands in your vault

Each save becomes `Instagram Saves/<Category>/YYYY-MM-DD <Topic Title>.md`:

- **Frontmatter** for querying: `title`, `shortcode`, `author`, `url`, `posted`, `category`, `video`, `duration_seconds`, `language`, `status`, `tags`
- The **video itself**, copied into the vault and embedded playable (named `YYYY-MM-DD <topic>.mp4`, not a random shortcode)
- **Summary** and **key points** written by Claude after watching keyframes and reading the transcript
- **Research & links** — Claude runs a few targeted web searches on the topic and the tools/people mentioned, returning vetted links with one-line relevance notes
- The original caption, and the full **Claude-corrected transcript** (Whisper's "quantrators" becomes "quant traders")

Two indexes are maintained: `_Index.md` (a human-readable table) and `_index.json` — one JSON record per save (shortcode, title, category, tags, summary, research links, note path, video path, URL) that **other AI agents can load directly** instead of parsing markdown.

## Requirements

- Windows 10/11 (Task Scheduler integration; the Python pipeline itself is portable)
- Python 3.10+
- An Instagram account, and a browser that's logged into it
- For the AI review step, one of:
  - [Claude Code](https://docs.claude.com/en/docs/claude-code) on your PATH (uses your Claude subscription; enables the web-research step), or
  - an `ANTHROPIC_API_KEY` environment variable, or
  - nothing — notes are then filed under `_Unreviewed` with the raw transcript and can be reprocessed later

ffmpeg and Whisper are installed automatically (bundled ffmpeg via `imageio-ffmpeg`, local transcription via `faster-whisper` — no audio ever leaves your machine).

## Install

```powershell
git clone https://github.com/<you>/ig-saves-to-vault
cd ig-saves-to-vault
powershell -ExecutionPolicy Bypass -File setup.ps1
```

The setup script creates a venv, installs dependencies, asks for your vault path, imports your Instagram session (from Firefox automatically, from an exported `cookies.txt`, or from a pasted `sessionid` cookie — your password is never touched), registers the daily scheduled task, and offers a 2-video test run.

Set your vault location in `config.json` (`vault_path`). Everything else has sensible defaults.

## Everyday commands

You shouldn't need any — the scheduled task handles everything. When you do:

```
venv\Scripts\python pipeline.py --dry-run        # show what a run WOULD do
venv\Scripts\python pipeline.py --limit 2        # small manual run
venv\Scripts\python pipeline.py --no-download    # process the backlog only (no Instagram contact)
venv\Scripts\python pipeline.py --backfill 40    # dig 40 posts deeper into older saves
venv\Scripts\python pipeline.py --reprocess SC   # redo the Claude step for one item
venv\Scripts\python pipeline.py --reprocess all  # regenerate every note (e.g. after changing the template)
```

Reprocessing replaces notes in place — both indexes are fully rebuilt each time, so nothing goes stale.

## Configuration (`config.json`)

| Key | Default | Meaning |
| --- | --- | --- |
| `vault_path` | `auto` | Obsidian vault root (`auto` = two levels above the install dir) |
| `notes_folder` | `Instagram Saves` | Where notes are filed inside the vault |
| `max_downloads_per_run` | `8` | Hard cap on downloads per run |
| `min/max_delay_seconds` | `25` / `70` | Jittered pause between posts |
| `scan_limit` / `stop_after_known_streak` | `50` / `5` | Feed-scan bounds |
| `initial_backfill_limit` | `30` | How deep the very first run looks |
| `whisper_model` | `small` | `base` / `small` / `medium` (multilingual, auto-detect) |
| `whisper_device` | `auto` | GPU if available, CPU fallback is automatic |
| `frame_count` | `5` | Evenly-spaced keyframes shown to Claude (≤12) |
| `brain` | `auto` | `claude-cli` / `api` / `basic` |
| `copy_video_to_vault` | `true` | Embed the mp4 in the vault |
| `keep_videos` | `true` | Keep originals in the working folder |

GPU transcription: `venv\Scripts\pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` — detected and used automatically on the next run.

## How the AI step works

Claude never writes files. It reads the transcript, metadata, and keyframes (plus a few web searches in Claude Code mode), and returns a single JSON object — title, summary, key points, category, tags, research links, corrected transcript. The Python pipeline is the only thing that touches your vault, writing every note from the same template. That separation keeps the output format perfectly uniform and makes the pipeline immune to whatever plugins or hooks your local Claude Code setup runs.

## Troubleshooting

- **"Another run is in progress"** — a previous run is genuinely still going, or was killed; stale locks from dead processes clear themselves.
- **`_ATTENTION Instagram Pipeline.md` appeared in your vault** — the Instagram session expired. Run `venv\Scripts\python import_session.py` again.
- **Items stuck as `failed:brain`** — check `logs/`; they retry automatically on the next run.
- **Two posts always fail with "Fetching Post metadata failed"** — Instagram refuses metadata for some posts (deleted/restricted); they're parked in `state.json` and never retried aggressively.

## Acknowledgements

Built on the shoulders of [Instaloader](https://instaloader.github.io/), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), and [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg). The dense keyframe-sampling idea for letting Claude "watch" videos was inspired by [claude-video](https://github.com/bradautomates/claude-video).

## License

[MIT](LICENSE)
