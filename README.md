# shortsmith

Self-hosted "AI clipper" (an Opus Clip / Ssemble-style tool) that runs from the command line. Give it a long video
and it writes vertical 9:16 shorts with word-by-word captions, a virality score, a title, a "why it hooks" note,
and a platform caption with hashtags for each clip. It also writes an HTML gallery and an optional dry-run
posting plan.

```
shortsmith "https://www.youtube.com/watch?v=VIDEO_ID" --count 5 --min 30 --max 60 --style hormozi --out ./out
```

> **Only clip content you have the rights to:** your own videos, Creative Commons (CC-BY) material with
> attribution, or creators' official clipping campaigns (Whop/Vyro-style programs that pay clippers). Re-uploading
> other people's content without permission infringes copyright and breaks platform rules.

## Install

Requirements: Linux/macOS, Python ≥ 3.10, `ffmpeg`/`ffprobe`, and `yt-dlp`. For reliable YouTube downloads, put
`deno` on PATH or in `~/.local/bin`, which yt-dlp uses to solve YouTube's JS challenges.

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
mkdir -p models && curl -L -o models/face_detection_yunet_2023mar.onnx \
  https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
# optional, for YouTube: deno
curl -fsSL https://deno.land/install.sh | sh
```

Fonts used by the presets are Poppins Black, Luckiest Guy, and Inter, all from Google Fonts. If one is missing,
libass falls back to another font, so install the fonts for the intended look.

The LLM used for clip selection is any OpenAI-compatible endpoint. The default is OpenCode Go:

| env var | default |
|---|---|
| `OPENCODE_GO_API_KEY` | read from the environment, or from `SHORTSMITH_ENV_FILE` (default `~/.hermes/.env`) |
| `SHORTSMITH_LLM_BASE_URL` | `https://opencode.ai/zen/go/v1` |
| `SHORTSMITH_LLM_MODEL` | `deepseek-v4.1-flash` |

If no key is set, or the LLM call fails, shortsmith picks clips with a built-in heuristic. Pass `--no-llm` to
use the heuristic on purpose.

## Usage

```
shortsmith SOURCE [--count 5] [--min 30] [--max 60] [--style hormozi|mrbeast|abdaal|my.json]
                  [--out DIR] [--model small] [--language en] [--reframe auto|face|center|letterbox]
                  [--no-llm] [--fresh] [--preset medium] [--schedule youtube,tiktok,instagram] [--every-hours 24]
shortsmith publish DIR [--platforms youtube,tiktok,instagram] [--every-hours 24]   # dry run
```

`SOURCE` can be any URL yt-dlp supports, or a local file. Downloads, transcripts, and LLM picks are cached in
`--cache` (default `~/.cache/shortsmith`), so a re-run with another style only re-renders. Pass `--fresh` to ask
the LLM again.

Output in `DIR`:
- `NN-title.mp4`: 1080×1920, H.264 High profile, yuv420p, AAC 160k at 48 kHz, loudness normalized to -14 LUFS,
  faststart
- `NN-title.jpg`: a thumbnail
- `clips.json`: title, virality_score, duration, start/end in the source, why_it_hooks, caption, hashtags,
  transcript, and the reframe mode used
- `index.html`: a gallery of the clips
- `publish_plan.json`: written only if you pass `--schedule` or run `shortsmith publish`

## Caption styles

| style | look |
|---|---|
| `hormozi` | Poppins Black in uppercase, white with the active word in yellow and slightly enlarged, thick black outline, 3 words per line, lower-middle of the frame |
| `mrbeast` | Luckiest Guy in uppercase, 2 words per line, active-word colour cycles green/yellow/red, heavy outline |
| `abdaal` | Inter Bold in sentence case, clean and minimal, inactive words dimmed, active word with a blue glow, up to 4 words per line |
| custom | a JSON file overriding any key of a base preset, e.g. `styles/custom-example.json` |

Custom style keys: `base`, `font`, `size`, `bold`, `upper`, `primary`, `highlight`, `highlight_cycle`, `outline`,
`outline_w`, `shadow`, `pos_y` (0 to 1 of frame height), `max_words`, `pop` (% scale of the active word),
`highlight_bg`, `highlight_bg_color`, `dim`, `char_w`.

## How it works

1. **Download.** yt-dlp fetches the video (≤1080p mp4) and caches it by video id.
2. **Transcribe.** faster-whisper runs on CPU with int8 and VAD, and returns word-level timestamps. The model is
   set with `--model`. `small` is a good balance on CPU and handled an 18-minute video in about 1:45 on 8 cores.
3. **Select.** The timestamped transcript goes to the LLM, which returns 2×count candidates as JSON: start, end,
   title, score, why, caption, and hashtags. The prompt asks it to start on a hook and end on a complete thought.
   Each candidate's start is then snapped to the nearest sentence start, and its end to a sentence end that keeps
   the clip within min..max. If no sentence end fits, the end goes to the longest pause instead. Overlapping
   candidates are dropped, and the top `count` by score are kept. The fallback heuristic scores every
   sentence-aligned window by hook words in the first ~12 words, questions, and speech density.
4. **Reframe.**
   - The clip is analysed at 960 px wide and 6 fps with the YuNet face detector, falling back to Haar.
     Detections are linked into tracks by position.
   - The active speaker is found from mouth-region frame differences, minus eye-region motion to cancel head
     turns, weighted by audio loudness.
   - A minimum shot length (3 s) and a switch ratio (1.6×) stop the frame from flickering between speakers. The
     camera cuts between speakers and follows the face smoothly within a shot, using a dead zone plus an EMA.
   - If no face is found, the source is letterboxed over a blurred copy of itself. Already-vertical sources are
     only scaled.
5. **Captions.** Words are grouped into lines of 2–4, breaking at punctuation and pauses. Each word gets one ASS
   event showing the whole line with that word highlighted. Long lines are shrunk to fit the frame width.
6. **Render.** Frames are cropped and scaled with OpenCV and piped to ffmpeg, which burns in the ASS subtitles
   with libass and encodes with x264 and AAC.

## Publishing / scheduling (stub)

Live posting is **not implemented** and is disabled by default. `shortsmith/publish.py` defines a `Publisher`
interface with `YouTubeShorts`, `TikTok`, and `InstagramReels` classes, and documents the API for each one:
YouTube Data API `videos.insert` with `publishAt`, the TikTok Content Posting API, and the Instagram Graph API
`media` and `media_publish` calls. The dry run writes `publish_plan.json`, listing what would be posted, where,
and when, one clip every `--every-hours`, highest score first.

To add real posting, implement `upload()` and set `SHORTSMITH_ENABLE_<PLATFORM>=1`. Keep OAuth tokens in env vars
or a secrets store, never in this repo.

## Limitations

- The active-speaker cue is visual only and has no audio diarization. Off-camera speakers, people facing away
  from the camera, and heavy gesturing can fool it. Use `--reframe center` or `--reframe letterbox` to override
  it.
- Upscaling a 608×1080 crop to 1080×1920 makes the frame softer. Use a 4K source when one is available.
- Wide two-shots are never split-screened. The frame shows one speaker at a time.
- Virality scores are the LLM's opinion and are not trained on real engagement data.
- Caption accuracy is limited by Whisper. Use `--model medium` or `--model large-v3` for better accuracy at the
  cost of speed.
- Very long videos (over about 2 hours) are subsampled to fit the LLM context.
