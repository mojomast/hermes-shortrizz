# picks.json

A Desktop preview pane edits this file before any encode. `shortrizz` (shorts) and `shortrizz highlights` both write it. Schema version is **1**.

## Files

| file | role |
|---|---|
| `<run>/picks.json` | Current revision. Overwritten on every save. |
| `<run>/picks.vN.json` | Immutable snapshot. `N` starts at 1 and increases only when the document content changes. |
| `<run>/stills/<id>.jpg` | One small JPEG per pick, grabbed with ffmpeg `-ss` before `-i` at the midpoint of `start`/`end`, scaled to 360 px wide. |

`revision` on the document is the snapshot number. Comparing revisions ignores that field: an unchanged document does not allocate a new `N`. Editing `picks.json` in place and saving again (including a render) writes the next snapshot, so the pane can show history and undo by loading an older `picks.vN.json` and saving it as the new head.

A failed still leaves `still` as `null` and does not fail selection.

## Document

```json
{
  "version": 1,
  "revision": 1,
  "run_id": "run-20261109-120000",
  "kind": "shorts",
  "created": "2026-11-09T17:00:00Z",
  "source": {
    "title": "Source title",
    "url": "https://example.com/watch",
    "video_path": "/absolute/path/to/source.mp4",
    "duration": 754.2,
    "meta": {}
  },
  "settings": {},
  "picks": []
}
```

| field | meaning |
|---|---|
| `version` | Always `1` for this contract. Other values are rejected. |
| `revision` | Snapshot number matching `picks.vN.json`. |
| `run_id` | The run directory name. |
| `kind` | `shorts` or `highlights`. Render a shorts file with `shortrizz --picks`, a highlights file with `shortrizz highlights --picks`. |
| `created` | UTC timestamp of the first selection. |
| `source.video_path` | Absolute path to the media that will be encoded. Required. |
| `source.meta` | Download metadata kept so a later render can rebuild the gallery. The pane can ignore it. |
| `settings` | Resume knobs from selection (style, model, cache, watermark dict, edit options, and for highlights the crossfade, captions, loudnorm, order). A render uses these unless the CLI or tool passes an override. |

## Pick

| field | type | meaning |
|---|---|---|
| `id` | string | Stable id, `p01`, `p02`, … Unique inside the file. |
| `order` | number | Play order. Enabled picks render lowest `order` first, then by `id`. |
| `enabled` | bool | `false` skips the pick. Missing means enabled. |
| `start` | number | Trim in, seconds on the source. `end` must be greater than `start`. |
| `end` | number | Trim out, seconds on the source. |
| `score` | number | Virality score, 0–100. |
| `title` | string | On-screen / card title. A render uses this text. |
| `hook` | string | Why the moment hooks. Carried as `why_it_hooks`. It is not forced into the on-screen hook line. |
| `transcript` | string | Snippet of the spoken words, at most 280 characters. |
| `caption_style` | string | `hormozi`, `mrbeast`, `abdaal`, or a style JSON path. A render uses this per pick. |
| `still` | string or null | Relative path `stills/<id>.jpg`, or `null` when the frame could not be grabbed. |
| `caption` | string | Suggested post caption. Optional for the pane. |
| `hashtags` | string[] | Suggested tags. |
| `method` | string | How the pick was chosen (`llm`, `heuristic`, …). |
| `word_range` | `[int, int]` or null | Inclusive word indexes into the transcript. A render keeps them when the trim still covers those words. A trim that cuts into the words drops the words that fall outside. |

## Render rules

`render_from_picks` (CLI `--picks`, tool `stage: "render"`):

1. Load the file and reject a bad version, a missing `source.video_path`, a duplicate id, or `end <= start`.
2. Save, which refreshes a still when `start` or `end` changed or the JPEG is missing, and writes `picks.vN.json` when the content changed.
3. Encode **enabled** picks in **order**.
4. Honour each pick's `start`, `end`, `title`, and `caption_style`.
5. Write `clips.json` and the gallery next to the output. With no `--out`, that folder is the picks directory.

`stage: "all"` (the default) and a CLI run without `--select-only` still select and encode in one shot. They also write `picks.json` before the encode. `--select-only` / `stage: "select"` returns after the picks and stills, with progress `stage` set to `picks_ready`. `shortrizz_status` then reports `stage: "picks_ready"`, `status: "done"`, and one summary per pick (`id`, `order`, `enabled`, `start`, `end`, `score`, `title`, `hook`, `caption_style`, absolute `still`).

Plugin paths: `out_dir` and `picks_path` must resolve inside `SHORTRIZZ_RUNS` (default `~/shortrizz-runs`). `cache_dir` must resolve inside `SHORTRIZZ_CACHE` (default `~/.cache/shortrizz`). The CLI `--out` flag is not confined.
