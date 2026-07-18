You are processing ONE downloaded Instagram save inside an Obsidian vault.
Your working directory is the vault root. Work only inside this vault.

## Input files (read all of them first)

- `{INBOX_DIR}/meta.json` — post metadata (author, caption, url, duration, language)
- `{INBOX_DIR}/transcript.txt` — Whisper transcript (may contain speech-recognition errors)
- `{INBOX_DIR}/frames/` — up to 3 keyframe images; Read them to see what the video shows

## Your job

1. **Verify the transcript.** Fix obvious speech-recognition errors using context
   from the caption and keyframes (misheard names, products, technical terms).
   Do NOT invent, add, or drop content. Keep the original language.
   If the transcript says `[no audio track]` or `[no speech detected]`, base the
   note on the caption and keyframes instead.
2. **Categorize.** Existing categories in this vault: {EXISTING_CATEGORIES}.
   Prefer an existing category; only create a new one (Title Case, 1–2 words)
   if nothing fits. Never create near-duplicates of existing categories.
3. **Write the note** to `{NOTES_FOLDER}/<Category>/{TODAY} <Short Title>.md`
   (create the category folder if needed; no characters illegal on Windows:
   `< > : " / \ | ? *`).
   **Title rule:** the title must state the actual TOPIC of the video — the
   specific technique, claim, tool, or lesson it teaches (e.g. "Backtest loop
   for quant trading strategies", "Fermented garlic honey method") — never a
   caption fragment, never hashtags, never clickbait like "This changed
   everything". 4–9 words. Use exactly this structure:

```markdown
---
title: "<title>"
source: instagram
author: "<author from meta.json>"
url: {URL}
posted: <posted date from meta.json>
processed: {TODAY}
category: "<Category>"
status: reviewed
tags:
  - instagram/save
  - <3-6 lowercase topical tags>
---

# <title>

> [!abstract] Summary
> <2–3 sentence summary of what the video teaches/shows and why it's worth keeping>

- <2–6 key takeaways as bullets>

**Author:** @<author> · **Posted:** <date> · [Watch on Instagram]({URL})

{VIDEO_EMBED}

> [!quote]- Original caption
> <caption from meta.json, trimmed to ~600 chars; omit this callout if empty>

## Transcript

<the corrected transcript>
```

4. **Update the index.** Append one row to `{NOTES_FOLDER}/_Index.md`
   (`| {TODAY} | [[<note filename without .md>]] | @<author> | <Category> | reviewed |`).
   If the file doesn't exist, create it with a frontmatter block
   (`tags: [instagram/index]`), a `# Instagram saves index` heading and the
   table header `| Date | Note | Author | Category | Status |`.
5. **Write the result receipt** — create `{INBOX_DIR}/result.json` containing
   exactly: `{"note_path": "<vault-relative path of the note you wrote>",
   "category": "<Category>", "title": "<title>", "tags": [<tags>]}`.
   This file is how the pipeline knows you succeeded — do not skip it.

Shortcode of this item: {SHORTCODE}. Do not modify any other notes in the
vault. Do not delete anything.
