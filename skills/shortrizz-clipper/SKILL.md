---
name: shortrizz-clipper
description: >-
  Skill id shortrizz:shortrizz-clipper. Load it with
  skill_view("shortrizz:shortrizz-clipper"). Turn a long video (YouTube or any
  yt-dlp URL, or a local file) into captioned vertical 9:16 shorts for YouTube
  Shorts, TikTok and Reels: best 30-60 s moments, face-tracked reframe,
  word-by-word captions, virality score, title, why-it-hooks and a caption with
  hashtags. Call the shortrizz_* tools. Jobs run in the background; poll
  shortrizz_status. Publishing is a dry-run plan only.
version: "0.1.0"
author: mojomast
license: MIT
compatibility: >-
  Python 3.10+, ffmpeg and ffprobe on PATH. CPU only. deno on PATH makes
  YouTube downloads reliable. The LLM picker reads OPENCODE_GO_API_KEY from the
  Hermes .env; without it a heuristic picks clips.
metadata:
  hermes:
    tags: [video, shorts, clipping, captions, whisper]
    category: media
    related_skills: []
---

# Shortrizz: long video to shorts

## Rights first
Only clip videos the user owns, has permission for, that are Creative Commons
(credit the creator), or that belong to an official clipping campaign. If the
user asks to clip someone else's video with no sign of permission, say so before
you start.

## Flow (about 3-6 tool calls)
1. Optional: `shortrizz_list_styles` if the user has not picked a style.
   Presets: `hormozi` (bold uppercase, yellow active word, the default),
   `mrbeast` (chunky, colourful), `abdaal` (clean minimal), or a JSON file path.
2. `shortrizz_make_shorts` with `source`, plus any of `count` (default 5),
   `min_seconds`/`max_seconds` (default 30/60), `style`, `reframe`
   (`auto` face tracking, `center`, `letterbox`). It returns a `job_id` and
   `out_dir` straight away.
3. `shortrizz_status` with `job_id` and `wait_seconds: 240`. Repeat while
   `status` is `running`. Progress shows `stage` (download, transcribe, select,
   render) and `done/total` clips. Timing on 8 CPU cores: transcription takes
   about 1/10 of the video's length (an 18 min video takes about 2 min), the
   LLM pick takes about 1 min, and each clip renders in about 25 s. A cached
   video only re-renders.
4. When `status` is `done`, report each clip: file path, title, virality score,
   duration, why it hooks and the suggested caption with hashtags. Also give the
   `gallery` (index.html) path.
5. Optional: `shortrizz_render_style` with `run_dir` (the finished `out_dir`)
   and a new `style` re-renders the same clips without re-transcribing.
6. Optional: `shortrizz_publish_plan` writes a dry-run schedule to
   `publish_plan.json`. It never uploads. Live posting to YouTube, TikTok or
   Instagram is not implemented. Do not try to upload the clips with other tools
   unless the user explicitly asks and names the account.

## Rules
- Do not run the CLI in a terminal when the tools are available. Do not try to
  install CUDA or GPU packages, because Shortrizz is CPU only.
- If a job fails, read `log_tail`. Common causes are a bad URL, a private or
  age-gated video, or a file with no audio track. Tell the user the cause and
  do not keep retrying the same input.
- `cancel: true` on `shortrizz_status` stops a job.
- Never print or ask for the API key. It is read from the Hermes `.env`.
