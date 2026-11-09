"""Shortrizz Studio: runs, storyboard edits, proxy previews, and render jobs.

The Desktop pane calls this through ``dashboard/plugin_api.py``. Importing this
module does not import OpenCV or faster-whisper. Every file the pane can read
resolves inside ``SHORTRIZZ_RUNS``. Responses name ``OPENCODE_GO_API_KEY`` only
as a presence flag — the value is never included.
"""
from __future__ import annotations

import base64, json, re, shutil, subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import captions, hermes_tools as H, picks
from .secrets import get_secret

MEDIA_MAX = 24 * 1024 * 1024
PROXY_WIDTH = 360
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")
_MEDIA = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}
_DROP_KEYS = {"video_path", "meta", "cache", "argv", "python", "log", "log_tail", "env"}
_SECRET_KEYS = {"api_key", "apikey", "token", "secret", "authorization", "password"}
SELECTING_STAGES = {"starting", "download", "transcribe", "select"}


class StudioError(Exception):
    def __init__(self, message: str, fix: str = ""):
        super().__init__(message)
        self.message = message
        self.fix = fix


def _secret_values():
    value = get_secret("OPENCODE_GO_API_KEY")
    if value and len(value) >= 8:
        return [value]
    return []


def scrub(value, secrets=None):
    """Drop path/meta keys the pane does not need, and blank any secret value."""
    if secrets is None:
        secrets = _secret_values()
    return _scrub(value, secrets)


def _scrub(value, secrets):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            low = str(key).lower()
            if key in _DROP_KEYS or low in _SECRET_KEYS:
                continue
            if key == "opencode_go_api_key" and isinstance(item, dict):
                out[key] = {"name": "OPENCODE_GO_API_KEY", "set": bool(item.get("set"))}
                continue
            out[key] = _scrub(item, secrets)
        return out
    if isinstance(value, list):
        return [_scrub(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret and secret in value:
                value = value.replace(secret, "[redacted]")
        return value
    return value


def stage_from(*, raw, alive, has_picks, has_output):
    """Map a progress.json stage plus liveness onto the five Studio stages."""
    raw = raw or ""
    if alive:
        if raw == "render":
            return "rendering"
        if raw == "done":
            return "done"
        if raw == "picks_ready":
            return "picks_ready"
        return "selecting"
    if raw == "picks_ready":
        return "picks_ready"
    if raw == "done":
        return "done"
    if raw in SELECTING_STAGES or raw == "render":
        return "error"
    if has_output:
        return "done"
    if has_picks:
        return "picks_ready"
    return "error"


def snap_edge(words, time, edge, slack=0.45):
    """Snap ``time`` to the nearest word start or end within ``slack`` seconds."""
    t = float(time)
    best = None
    best_d = slack + 1.0
    for w in words or []:
        if not isinstance(w, dict):
            continue
        try:
            point = float(w["e"] if edge == "end" else w["s"])
        except (KeyError, TypeError, ValueError):
            continue
        dist = abs(point - t)
        if dist < best_d - 1e-9 or (abs(dist - best_d) < 1e-9 and best is not None and point < best):
            best, best_d = point, dist
    if best is None or best_d > slack + 1e-9:
        return round(t, 3)
    return round(best, 3)


def proxy_rel(pick_id, start, end) -> str:
    if not re.fullmatch(r"p\d{2,}", str(pick_id or "")):
        raise StudioError("Unknown pick.")
    a = int(round(float(start) * 1000))
    b = int(round(float(end) * 1000))
    if b <= a:
        raise StudioError("That pick ends at or before it starts.")
    return f"previews/{pick_id}_{a}_{b}.mp4"


def proxy_ffmpeg_args(src, start, end, dest) -> list[str]:
    """360p ultrafast proxy. No captions — the pane draws those in CSS."""
    dur = max(0.05, float(end) - float(start))
    return [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{float(start):.3f}",
        "-i", str(src),
        "-t", f"{dur:.3f}",
        "-vf", f"scale={PROXY_WIDTH}:-2,format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32",
        "-c:a", "aac", "-ac", "1", "-b:a", "64k",
        "-movflags", "+faststart",
        str(dest),
    ]


def encode_proxy(src, start, end, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".partial.mp4")
    proc = subprocess.run(proxy_ffmpeg_args(src, start, end, tmp), capture_output=True, text=True)
    if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size <= 0:
        tmp.unlink(missing_ok=True)
        raise StudioError(
            "Could not build a preview.",
            "ffmpeg could not read that segment. The still and caption preview still work.",
        )
    tmp.replace(dest)


def _has_ytdlp() -> bool:
    if shutil.which("yt-dlp"):
        return True
    import importlib.util
    return importlib.util.find_spec("yt_dlp") is not None


def _deps():
    return {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "yt_dlp": _has_ytdlp(),
        "opencode_go_api_key": {"name": "OPENCODE_GO_API_KEY", "set": bool(get_secret("OPENCODE_GO_API_KEY"))},
    }


def _styles():
    described = captions.describe_styles()["builtin"]
    return [{"id": name, "description": info.get("description") or "", "font": info.get("font") or ""}
            for name, info in described.items()]


def health():
    return scrub({"ok": True, "deps": _deps(), "styles": _styles()})


def _read_json(path: Path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _run_dir(run_id: str) -> Path:
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise StudioError("Unknown run.")
    root = H.runs_root()
    run = (root / run_id).resolve()
    try:
        run.relative_to(root)
    except ValueError:
        raise StudioError("That run is outside the runs folder.") from None
    if run.parent != root or not run.is_dir():
        raise StudioError("That run does not exist.")
    return run


def _confine_rel(run: Path, rel: str) -> Path:
    if not isinstance(rel, str) or not rel.strip() or "\x00" in rel or "\\" in rel:
        raise StudioError("That file is outside this run.")
    raw = Path(rel)
    if raw.is_absolute() or ".." in raw.parts:
        raise StudioError("That file is outside this run.")
    root = run.resolve()
    target = (root / raw).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise StudioError("That file is outside this run.") from None
    return target


def _jobs_for(run: Path):
    root = run.resolve()
    folder = H._jobs_dir()
    found = []
    if not folder.is_dir():
        return found
    for path in folder.glob("*.json"):
        job = _read_json(path)
        out = job.get("out_dir")
        if not out:
            continue
        try:
            Path(out).expanduser().resolve().relative_to(root)
        except (OSError, ValueError):
            continue
        found.append(job)
    return found


def _progress_files(run: Path):
    files = []
    if (run / "progress.json").is_file():
        files.append(run / "progress.json")
    renders = run / "renders"
    if renders.is_dir():
        for child in renders.iterdir():
            prog = child / "progress.json"
            if child.is_dir() and prog.is_file():
                files.append(prog)
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def _active_job(run: Path):
    for job in _jobs_for(run):
        try:
            pid = int(job.get("pid") or 0)
        except (TypeError, ValueError):
            continue
        if pid and H._pid_alive(pid):
            return job
    return None


def _output_dirs(run: Path):
    dirs = []
    if (run / "clips.json").is_file():
        dirs.append(run)
    renders = run / "renders"
    if renders.is_dir():
        children = [p for p in renders.iterdir() if p.is_dir()]
        children.sort(key=lambda p: p.stat().st_mtime)
        dirs.extend(children)
    return dirs


def _clip_rows(run: Path, folder: Path):
    doc = _read_json(folder / "clips.json")
    rows = []
    for clip in doc.get("clips") or []:
        if not isinstance(clip, dict):
            continue
        name = clip.get("file") or doc.get("file")
        if not name or not isinstance(name, str) or "/" in name or "\\" in name or name.startswith("."):
            continue
        rel = name if folder == run else f"renders/{folder.name}/{name}"
        rows.append({
            "title": clip.get("title") or "Clip",
            "duration": clip.get("duration"),
            "score": clip.get("virality_score"),
            "path": rel,
        })
    return rows, doc


def _render_rows(run: Path):
    rows = []
    for folder in _output_dirs(run):
        prog = _read_json(folder / "progress.json")
        marker = _read_json(folder / "studio.json")
        clips, doc = _clip_rows(run, folder)
        if (folder / "clips.json").is_file():
            status = "done"
        elif prog.get("stage") == "render":
            status = "rendering"
        elif prog.get("stage") in ("download", "transcribe", "select", "starting"):
            status = "rendering"
        elif prog:
            status = "error"
        else:
            status = "empty"
        rows.append({
            "id": "original" if folder == run else folder.name,
            "status": status,
            "style": marker.get("style") or (doc.get("settings") or {}).get("style") or "",
            "kind": marker.get("kind") or ("highlights" if doc.get("format") == "highlights" else "render"),
            "progress": {"done": int(prog.get("done") or 0), "total": int(prog.get("total") or 0)},
            "clips": clips,
        })
    return rows


def _load_words(run: Path):
    path = run / "words.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _words_near(words, start, end, pad=2.0, limit=80):
    lo, hi = float(start) - pad, float(end) + pad
    rows = []
    for w in words:
        if not isinstance(w, dict):
            continue
        try:
            s, e = float(w["s"]), float(w["e"])
        except (KeyError, TypeError, ValueError):
            continue
        if e < lo or s > hi:
            continue
        rows.append({"w": str(w.get("w") or ""), "s": round(s, 3), "e": round(e, 3)})
        if len(rows) >= limit:
            break
    return rows


def _public_pick(pick, words):
    start, end = float(pick["start"]), float(pick["end"])
    still = pick.get("still") if isinstance(pick.get("still"), str) else None
    if still and (".." in Path(still).parts or Path(still).is_absolute()):
        still = None
    return {
        "id": pick.get("id"),
        "order": int(pick.get("order") or 0),
        "enabled": bool(pick.get("enabled", True)),
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(end - start, 3),
        "score": pick.get("score") or 0,
        "title": pick.get("title") or "Clip",
        "hook": pick.get("hook") or "",
        "transcript": pick.get("transcript") or "",
        "caption_style": pick.get("caption_style") or "hormozi",
        "still": still,
        "words": _words_near(words, start, end),
    }


def _versions(run: Path):
    return [{"revision": n} for n in sorted(picks._revision_numbers(run))]


def _source_public(doc):
    source = doc.get("source") if isinstance(doc.get("source"), dict) else {}
    url = source.get("url") or ""
    if not isinstance(url, str) or url.startswith("file:"):
        url = ""
    try:
        duration = float(source.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    return {"title": source.get("title") or "", "url": url, "duration": round(duration, 3)}


def _error_text(run: Path, stage: str):
    if stage != "error":
        return ""
    for job in _jobs_for(run):
        log = job.get("log")
        if not log:
            continue
        try:
            lines = Path(log).read_text(errors="replace").splitlines()[-8:]
        except OSError:
            continue
        text = " ".join(line.strip() for line in lines if line.strip())
        text = scrub(text)
        if text:
            return text[:500]
    return "The job stopped before it finished."


def _classify(run: Path):
    picks_path = run / "picks.json"
    has_picks = picks_path.is_file()
    renders = _render_rows(run)
    has_output = any(row["status"] == "done" for row in renders)
    job = _active_job(run)
    prog_path = None
    if job:
        cand = Path(str(job.get("out_dir") or "")) / "progress.json"
        if cand.is_file():
            prog_path = cand
    if prog_path is None:
        files = _progress_files(run)
        prog_path = files[0] if files else None
    prog = _read_json(prog_path) if prog_path else {}
    raw = str(prog.get("stage") or "")
    # A finished render folder newer than a picks_ready root is the one to show.
    if not job and renders and prog_path and prog_path.parent != run and raw == "done":
        stage = "done"
    else:
        stage = stage_from(raw=raw, alive=bool(job), has_picks=has_picks, has_output=has_output and raw != "picks_ready")
        if raw == "picks_ready":
            stage = "picks_ready"
        elif not job and raw == "render":
            stage = "error"
        elif job and raw == "render":
            stage = "rendering"
    return stage, prog, renders


def _title_kind(run: Path):
    doc = _read_json(run / "picks.json")
    if not doc:
        doc = _read_json(run / "clips.json")
    source = doc.get("source") if isinstance(doc.get("source"), dict) else {}
    title = source.get("title") or run.name
    kind = doc.get("kind") or ("highlights" if doc.get("format") == "highlights" else "")
    return str(title), str(kind or ""), doc


def list_runs():
    root = H.runs_root()
    rows = []
    if root.is_dir():
        for child in root.iterdir():
            if not child.is_dir() or not _RUN_ID.fullmatch(child.name):
                continue
            if child.is_symlink():
                continue
            interesting = (child / "picks.json").is_file() or (child / "progress.json").is_file() or (child / "clips.json").is_file()
            if not interesting:
                continue
            stage, prog, _renders = _classify(child)
            title, kind, _doc = _title_kind(child)
            stamp = max((p.stat().st_mtime for p in (child / "picks.json", child / "progress.json", child / "clips.json") if p.is_file()), default=child.stat().st_mtime)
            rows.append({
                "id": child.name,
                "stage": stage,
                "title": title,
                "kind": kind,
                "progress": {"done": int(prog.get("done") or 0), "total": int(prog.get("total") or 0)},
                "updated": datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "_stamp": stamp,
            })
    rows.sort(key=lambda row: row["_stamp"], reverse=True)
    for row in rows:
        row.pop("_stamp", None)
    return scrub({"ok": True, "runs": rows[:30]})


def get_run(run_id: str):
    run = _run_dir(run_id)
    stage, prog, renders = _classify(run)
    title, kind, raw_doc = _title_kind(run)
    doc = {}
    if (run / "picks.json").is_file():
        try:
            doc = picks.load_picks(run / "picks.json")
        except ValueError as e:
            return scrub({
                "ok": True,
                "run": {
                    "id": run.name, "stage": "error", "title": title, "kind": kind,
                    "progress": {"done": 0, "total": 0}, "source": {"title": title, "url": "", "duration": 0},
                    "revision": None, "versions": _versions(run), "picks": [], "renders": renders,
                    "error": str(e),
                },
            })
    words = _load_words(run)
    public_picks = [_public_pick(p, words) for p in (doc.get("picks") or [])]
    source = _source_public(doc or raw_doc)
    if not source["title"]:
        source["title"] = title
    payload = {
        "id": run.name,
        "stage": stage,
        "title": source["title"] or title,
        "kind": doc.get("kind") or kind,
        "progress": {"done": int(prog.get("done") or 0), "total": int(prog.get("total") or 0)},
        "source": source,
        "revision": doc.get("revision"),
        "versions": _versions(run),
        "picks": public_picks,
        "renders": renders,
        "error": _error_text(run, stage),
    }
    return scrub({"ok": True, "run": payload})


def read_media(run_id: str, rel: str):
    run = _run_dir(run_id)
    path = _confine_rel(run, rel)
    suffix = path.suffix.lower()
    if suffix not in _MEDIA:
        raise StudioError("That file is not a still or a video.")
    if not path.is_file():
        raise StudioError("That file is not in this run.")
    size = path.stat().st_size
    if size <= 0:
        raise StudioError("That file is empty.")
    if size > MEDIA_MAX:
        raise StudioError(
            "That file is too large to preview inline.",
            "Use Open folder to play it on disk.",
        )
    data = path.read_bytes()
    rel_out = path.relative_to(run.resolve()).as_posix()
    return scrub({
        "ok": True,
        "path": rel_out,
        "mime": _MEDIA[suffix],
        "data_b64": base64.b64encode(data).decode("ascii"),
    })


def _tool_result(raw: str):
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        raise StudioError("Studio could not start that job.") from None
    if not isinstance(data, dict):
        raise StudioError("Studio could not start that job.")
    if data.get("ok") is False:
        raise StudioError(str(data.get("error") or "That job did not start."), str(data.get("fix") or ""))
    out = str(data.get("out_dir") or "")
    run_id = Path(out).name if out else ""
    if not run_id:
        raise StudioError("Studio could not start that job.")
    return scrub({
        "ok": True,
        "run_id": run_id,
        "job_id": data.get("job_id") or "",
        "stage": "rendering" if data.get("stage") == "render" else ("selecting" if data.get("status") == "running" else data.get("stage") or "selecting"),
    })


def start_select(body: dict):
    body = dict(body or {})
    kind = body.get("kind") or "shorts"
    if kind not in ("shorts", "highlights"):
        raise StudioError('Choose shorts or highlights.')
    source = str(body.get("source") or "").strip()
    if not source:
        raise StudioError("Paste a video URL or a local file path.")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise StudioError("ffmpeg and ffprobe must be on PATH.", "Install ffmpeg, then start again.")
    if re.match(r"^https?://", source) and not _has_ytdlp():
        raise StudioError(
            "yt-dlp is not installed, so a URL cannot be downloaded.",
            "Install yt-dlp, or choose a local file.",
        )
    style = str(body.get("style") or "hormozi")
    if style not in captions.STYLES:
        raise StudioError("Choose hormozi, mrbeast or abdaal.")
    payload = {"source": source, "style": style, "stage": "select", "use_llm": True}
    if kind == "shorts":
        if body.get("count") is not None:
            payload["count"] = body["count"]
        raw = H.make_shorts_tool(payload)
    else:
        raw = H.make_highlights_tool(payload)
    result = _tool_result(raw)
    result["kind"] = kind
    result["stage"] = "selecting"
    return result


def _source_for_rerun(doc):
    source = doc.get("source") or {}
    url = source.get("url") or ""
    if isinstance(url, str) and re.match(r"^https?://", url):
        return url
    video = source.get("video_path") or ""
    if video and Path(video).expanduser().is_file():
        return str(Path(video).expanduser())
    raise StudioError("This run has no source URL or video to pick from again.")


def start_repick(run_id: str):
    run = _run_dir(run_id)
    if not (run / "picks.json").is_file():
        raise StudioError("This run has no picks yet.")
    doc = picks.load_picks(run / "picks.json")
    source = _source_for_rerun(doc)
    settings = doc.get("settings") or {}
    style = settings.get("style") if settings.get("style") in captions.STYLES else "hormozi"
    payload = {"source": source, "style": style, "stage": "select", "avoid_previous": True, "use_llm": True}
    if doc.get("kind") == "highlights":
        raw = H.make_highlights_tool(payload)
    else:
        if settings.get("count"):
            payload["count"] = settings["count"]
        raw = H.make_shorts_tool(payload)
    result = _tool_result(raw)
    result["stage"] = "selecting"
    result["repick"] = True
    return result


def save_version(run_id: str, body: dict):
    run = _run_dir(run_id)
    path = run / "picks.json"
    if not path.is_file():
        raise StudioError("This run has no picks yet.")
    doc = picks.load_picks(path)
    incoming = (body or {}).get("picks")
    if not isinstance(incoming, list) or not incoming:
        raise StudioError("Send the picks to save.")
    by_id = {p["id"]: p for p in doc["picks"]}
    words = _load_words(run) if body.get("snap", True) else []
    try:
        duration = float((doc.get("source") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    seen = set()
    updated = []
    for raw in incoming:
        if not isinstance(raw, dict) or raw.get("id") not in by_id:
            raise StudioError("A pick in that save is not part of this run.")
        if raw["id"] in seen:
            raise StudioError("Each pick can only appear once.")
        seen.add(raw["id"])
        base = dict(by_id[raw["id"]])
        try:
            start = float(raw.get("start", base["start"]))
            end = float(raw.get("end", base["end"]))
        except (TypeError, ValueError):
            raise StudioError(f"Pick {raw['id']} needs numeric start and end.") from None
        if words:
            start = snap_edge(words, start, "start")
            end = snap_edge(words, end, "end")
        if duration > 0:
            start = min(max(0.0, start), duration)
            end = min(max(0.0, end), duration)
        if end <= start:
            raise StudioError(f"Pick {base['id']} ends at or before it starts.")
        style = str(raw.get("caption_style") or base.get("caption_style") or "hormozi")
        if style not in captions.STYLES and style != base.get("caption_style"):
            raise StudioError("Choose hormozi, mrbeast or abdaal.")
        title = str(raw.get("title", base.get("title") or "Clip")).strip() or "Clip"
        if len(title) > 140:
            title = title[:140].rstrip()
        try:
            order = int(raw.get("order", base.get("order") or 0))
        except (TypeError, ValueError):
            raise StudioError(f"Pick {base['id']} needs a numeric order.") from None
        base["start"] = round(float(start), 3)
        base["end"] = round(float(end), 3)
        base["title"] = title
        base["caption_style"] = style
        base["enabled"] = bool(raw.get("enabled", base.get("enabled", True)))
        base["order"] = order
        updated.append(base)
    for pick in doc["picks"]:
        if pick["id"] not in seen:
            updated.append(dict(pick))
    updated.sort(key=lambda p: (int(p.get("order") or 0), str(p.get("id"))))
    for index, pick in enumerate(updated, 1):
        pick["order"] = index
    doc["picks"] = updated
    picks.save_picks(run, doc, refresh_stills=True)
    return get_run(run_id)


def restore_version(run_id: str, revision):
    run = _run_dir(run_id)
    try:
        number = int(revision)
    except (TypeError, ValueError):
        raise StudioError("Choose a picks version to restore.") from None
    path = run / f"picks.v{number}.json"
    if not path.is_file():
        raise StudioError(f"There is no picks version {number}.")
    doc = picks.load_picks(path)
    picks.save_picks(run, doc, refresh_stills=True)
    return get_run(run_id)


def preview(run_id: str, pick_id: str):
    run = _run_dir(run_id)
    if not (run / "picks.json").is_file():
        raise StudioError("This run has no picks yet.")
    if not shutil.which("ffmpeg"):
        raise StudioError("ffmpeg is not on PATH.", "Install ffmpeg, then preview the pick again.")
    doc = picks.load_picks(run / "picks.json")
    pick = next((p for p in doc["picks"] if p.get("id") == pick_id), None)
    if pick is None:
        raise StudioError("That pick is not in this run.")
    src = Path(str((doc.get("source") or {}).get("video_path") or "")).expanduser()
    if not src.is_file():
        raise StudioError("The source video is missing, so the preview cannot be built.")
    rel = proxy_rel(pick["id"], pick["start"], pick["end"])
    dest = _confine_rel(run, rel)
    if not dest.is_file() or dest.stat().st_size <= 0:
        encode_proxy(src, pick["start"], pick["end"], dest)
    return scrub({"ok": True, "run_id": run.name, "pick_id": pick["id"], "path": rel, "mime": "video/mp4"})


def _next_render_dir(run: Path) -> Path:
    root = run / "renders"
    nums = []
    if root.is_dir():
        for child in root.iterdir():
            match = re.fullmatch(r"v(\d+)", child.name)
            if match:
                nums.append(int(match.group(1)))
    dest = root / f"v{max(nums, default=0) + 1}"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _mark_render(dest: Path, kind: str, style: str = ""):
    (dest / "studio.json").write_text(json.dumps({"kind": kind, "style": style}, indent=2) + "\n")


def start_render(run_id: str, body=None):
    body = dict(body or {})
    if body.get("picks"):
        save_version(run_id, body)
    run = _run_dir(run_id)
    picks_path = run / "picks.json"
    if not picks_path.is_file():
        raise StudioError("This run has no picks yet.")
    doc = picks.load_picks(picks_path)
    if not picks.enabled_picks(doc):
        raise StudioError("Turn on at least one pick before rendering.")
    if not shutil.which("ffmpeg"):
        raise StudioError("ffmpeg is not on PATH.", "Install ffmpeg, then render again.")
    dest = _next_render_dir(run)
    payload = {"stage": "render", "picks_path": str(picks_path), "out_dir": str(dest)}
    if doc.get("kind") == "highlights":
        raw = H.make_highlights_tool(payload)
    else:
        raw = H.make_shorts_tool(payload)
    _mark_render(dest, "render")
    result = _tool_result(raw)
    result["render_id"] = dest.name
    result["stage"] = "rendering"
    result["run_id"] = run.name
    return result


def _latest_clips_dir(run: Path):
    found = None
    for folder in _output_dirs(run):
        if (folder / "clips.json").is_file():
            found = folder
    return found


def start_restyle(run_id: str, body=None):
    body = dict(body or {})
    style = str(body.get("style") or "")
    if style not in captions.STYLES:
        raise StudioError("Choose hormozi, mrbeast or abdaal.")
    run = _run_dir(run_id)
    base = _latest_clips_dir(run)
    if base is None:
        raise StudioError("Render the picks before restyling.")
    if _read_json(base / "clips.json").get("format") == "highlights":
        raise StudioError("Restyle renders vertical shorts. This render is a 16:9 highlights video.")
    if not shutil.which("ffmpeg"):
        raise StudioError("ffmpeg is not on PATH.", "Install ffmpeg, then restyle again.")
    dest = _next_render_dir(run)
    raw = H.render_style_tool({"run_dir": str(base), "style": style, "out_dir": str(dest)})
    _mark_render(dest, "restyle", style)
    result = _tool_result(raw)
    result["render_id"] = dest.name
    result["stage"] = "rendering"
    result["run_id"] = run.name
    result["style"] = style
    return result


def open_target(run_id: str):
    run = _run_dir(run_id)
    return scrub({"ok": True, "path": str(run)})
