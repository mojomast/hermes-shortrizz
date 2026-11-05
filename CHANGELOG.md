# Changelog

## [Unreleased]

## [0.3.0] - 2026-11-05

### Added
- **Long-form highlights.** `shortrizz highlights SOURCE` (Hermes tool `shortrizz_make_highlights`) builds one 16:9 video from the top-scoring moments of a source. It reuses the existing transcript, virality scores and sentence-boundary snap. Target duration, max segment count and a minimum score; overlapping and near-duplicate segments are dropped. Play order is chronological by default, or `--order score`. The source framing is kept (no vertical crop); resolution, frame rate and loudness are normalised; segments join with a short audio and video crossfade (`--crossfade`, or `--hard-cuts`). Captions are optional and laid out in the lower third (`--style`, `--no-captions`). Writes `chapters.txt` (output time, score, source time) and `highlights.json`.

## [0.2.1] - 2026-11-04

### Fixed
- Clips whose cold open comes from later in the source than the body (common with `--length tight --all-fx`) came out with no audio stream or silent audio. Each kept segment now reads its own copy of the source audio, and every render checks the output: it fails if the audio stream is missing or silent.
- The end card / narrator CTA no longer overlaps live captions: captions are hidden while it is on screen.
- Hook text avoids the whole head (hair, forehead and sides of the face, not just eyes and mouth). It tries the usual top spot, just under the top safe line, below the head and lower alternate spots, and picks the least overlap, weighting eyes-to-chin heaviest. The hook's height comes from the actual hook text.
- The cold open never ends mid-phrase: a long pick is shortened only to a punctuation or pause end (otherwise dropped), saved picks are re-validated, and padding no longer bleeds into neighbouring words.
- Transcript caching no longer fails on numpy values (e.g. `numpy.bool_` from the quality checks).
- YouTube downloads are reused from the cache by video id, and the yt-dlp id lookup is retried.

## [0.2.0] - 2026-11-04

### Added
- **Deeper edits, on by default:** dead-air tightening (gaps > 0.35 s → ~0.15 s, 0.08/0.12 s word padding, silero-vad aligned cut points, 15 ms equal-power crossfades, captions retimed); a second LLM pass that returns a validated word-index EDL (cached, so `restyle` needs no LLM); on-screen hook text in the first 0–2.8 s; two-pass loudnorm to −14 LUFS / −1.5 dBTP with an 80 Hz high-pass; platform safe zones for all text. `--edit off|light|normal|aggressive`, `--no-llm-edits`, `--no-hook-text`, `--no-loudnorm`, `--no-edits`.
- **Opt-in effects (default off):** `--cold-open`, `--tts-intro` / `--tts-outro` (Kokoro-82M via kokoro-onnx, stock voice, `[tts]` extra), `--zoom`, `--loop`, `--emoji`, `--sfx` (self-synthesised CC0 sounds), `--all-fx`, and `--length tight` (20–35 s). All are also plugin tool parameters and config keys.
- **Watermark (default off):** the sliced-play mark, enabled per user with `--watermark`, `SHORTRIZZ_WATERMARK=1` or `"watermark": true` in `~/.config/shortrizz/config.json`; `watermark` parameter on the make and render_style tools. Adaptive dark outline and backing pill keep it readable on white.
- New **sliced-play logo** across the logo, icon, banner and watermark (`scripts/make_brand.py`).
- Transcript sanity checks: loop detection and repair, sparse-speech and VAD speech-coverage checks; bad caches are redone.
- `--avoid-previous` (plugin `avoid_previous`) skips moments earlier runs on the same video used (logged in the cache); `--exclude RUN_DIR|clips.json` skips a specific run's moments.
- `CHANGELOG.md`.

### Changed
- Whisper runs in float32 by default (int8 dropped speech on some sources); `SHORTRIZZ_COMPUTE_TYPE` overrides. No conditioning on previous text, temperature fallback, compression/no-speech thresholds.
- Clip boundaries snap to sentence ends, then Whisper segment edges or pauses, so unpunctuated transcripts still give clean clips.
- Duplicate hashtags are removed (case-insensitive) and capped at 8.
- The edit renderer renders 60 fps sources at 30 fps; face tracking for text placement uses one 2 fps decode pass.
- Clips are written to `NAME.partial.mp4` and renamed when complete.

### Fixed
- The AI edit pass no longer cuts pronouns, connectives or meaning words ("I", "which is"); only fillers, exact repeats and restarts.
- Cold open now fires reliably: clause-level 2–5 s picks, trimming of long LLM picks, heuristic fallback.
- Narrator intro/outro and cold open no longer make clips longer than their source or length band: the body is trimmed at a sentence end or pause.
- Tight clips (`--length tight`) with all effects auto-skip the narrator intro/outro and shorten the cold open so the speaker fills at least ~70% of the runtime (logged; explicit `--tts-intro`/`--tts-outro` still force them).
- Hook text is face-aware; captions keep one position per shot (with hysteresis) instead of jumping between forehead and chest; emoji fall back to smaller sizes and corner spots instead of being skipped.
- Captions and emoji avoid faces; the narrator hold frame is the sharpest, steadiest frame with a clear face.
- Captions glue Whisper's split numbers ("$3 ,000" → "$3,000").
- A hung LLM call can no longer stall a render (hard 120 s deadline); frame-reader processes are closed when a segment ends early.
- `restyle` passes the edit options through and reuses saved EDLs.

## [0.1.0]
- First release: download, faster-whisper transcription, LLM/heuristic clip picks, face-tracked 9:16 reframe, karaoke caption styles, clips.json, gallery, dry-run publish plan, Hermes plugin.
