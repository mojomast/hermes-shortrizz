---
name: shortrizz-clipper
description: >-
  Skill id shortrizz:shortrizz-clipper. Load it with
  skill_view("shortrizz:shortrizz-clipper"). Turn a long video (YouTube or any
  yt-dlp URL, or a local file) into captioned vertical 9:16 shorts for YouTube
  Shorts, TikTok and Reels, or into one 16:9 highlights video
  (shortrizz_make_highlights) built from the top-scoring moments. Shorts get a
  face-tracked reframe, word-by-word captions, a virality score, a title,
  why-it-hooks and a caption with hashtags. Call the shortrizz_* tools. Jobs
  run in the background; poll shortrizz_status. Publishing is a dry-run plan only.
version: "0.6.0"
author: mojomast
license: MIT
compatibility: >-
  Python 3.10+, ffmpeg and ffprobe on PATH. CPU only. deno on PATH makes
  YouTube downloads reliable. The LLM picker reads OPENCODE_GO_API_KEY through
  the Hermes secret scope (the same name in the environment for the CLI).
  Shortrizz does not open a dotenv file. Without the key a heuristic picks clips.
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
   about 1/10-1/4 of the video's length (an 18 min video takes about 2-5 min), the
   LLM pick takes about 1 min, and each clip renders in about 1 min with edits. A cached
   video only re-renders.
4. When `status` is `done`, report each clip: file path, title, virality score,
   duration, why it hooks and the suggested caption with hashtags. Also give the
   `gallery` (index.html) path.
   Every clip also gets the default edit pass (dead-air tightening, AI removal
   of fillers/repeats/restarts, a hook line in the first 3 s, -14 LUFS loudness,
   safe-zone text placement off faces). Optional parameters: `edit`
   (`off|light|normal|aggressive`), `deeper_edits: false` for a plain render,
   `llm_edits`, `hook_text`, `loudnorm`, and the opt-in effects `cold_open`,
   `tts_intro`, `tts_outro` (AI narrator, stock voice; tell the user it is a
   synthetic voice), `zoom`, `loop`, `emoji`, `sfx`. `length: "tight"` gives
   20-35 s clips.    `watermark` burns in the Shortrizz mark (off unless the user's
   config turns it on). Each clip in the result reports its edits: seconds
   removed, cuts, effects and final length.
   Every short and every highlights file opens on a money shot: frame 0 is the
   strongest finished frame, with the hook or caption already readable, so the
   file works as the thumbnail. Audio is not shifted. It is on unless the user
   set `SHORTRIZZ_MONEY_SHOT=0` (also `false`, `off`, `no`) or `"money_shot": false`
   in the Shortrizz config. There is no tool parameter for it.
5. Optional: `shortrizz_render_style` with `run_dir` (the finished `out_dir`)
   and a new `style` re-renders the same clips without re-transcribing.
6. Optional: the Desktop pane **Shortrizz Studio** (Hermes >= 0.23.0) does the
   same review without new tools: storyboard, trim, save `picks.vN.json`, then
   render. If the pane is missing after install, the user rescans from
   Capabilities → Plugins.
7. Optional: `shortrizz_publish_plan` writes a dry-run schedule to
   `publish_plan.json`. It never uploads. Live posting to YouTube, TikTok or
   Instagram is not implemented. Do not try to upload the clips with other tools
   unless the user explicitly asks and names the account.

## Long-form highlights (16:9)
When the user wants one horizontal video instead of a feed of vertical shorts,
call `shortrizz_make_highlights` with `source`. Useful knobs: `duration`
(default 180 seconds), `max_clips` (default 8), `min_score` (default 50),
`min_seconds`/`max_seconds` (default 15/45), `order` (`chrono` by default so
the story stays in order, or `score`), `crossfade` (default 0.4; `hard_cuts`
for butt joins), `captions` (default on, lower third; the same `style` names),
`loudnorm` (default on). The reel opens on the same money shot as a short.
It returns a `job_id`. Poll `shortrizz_status` the
same way. When it finishes, report the highlights file, the duration, and each
segment's title, score and source time. `chapters.txt` and `highlights.json`
sit next to the video. This path does not reframe to 9:16 and does not run the
shorts edit effects. Do not offer `shortrizz_publish_plan` for a highlights run.

## Preview picks before encoding
To let the user look at moments before any encode, call `shortrizz_make_shorts`
or `shortrizz_make_highlights` with `stage: "select"`. Poll `shortrizz_status`
until `stage` is `picks_ready`. Show each pick's title, score, hook, start, end
and still path. The user can edit `<out_dir>/picks.json` (trim, title,
`caption_style`, `enabled`, `order`). Each save keeps `picks.vN.json`. Then call
the same tool with `stage: "render"` and `picks_path` set to that `picks.json`.
`stage` defaults to `all`, which still selects and renders in one job.
`out_dir`, `cache_dir` and `picks_path` must stay inside the plugin's runs and
cache folders. See `docs/PICKS_CONTRACT.md`.

## Rules
- Do not run the CLI in a terminal when the tools are available. Do not try to
  install CUDA or GPU packages, because Shortrizz is CPU only.
- If a job fails, read `log_tail`. Common causes are a bad URL, a private or
  age-gated video, or a file with no audio track. Tell the user the cause and
  do not keep retrying the same input.
- `cancel: true` on `shortrizz_status` stops a job.
- Never print or ask for the API key. It is `OPENCODE_GO_API_KEY`, read through the Hermes secret scope.
