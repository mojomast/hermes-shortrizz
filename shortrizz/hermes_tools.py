"""Hermes tools for Shortrizz.

Rendering takes minutes, so make/render tools start a background job (a detached
``python -m shortrizz`` process) and return a ``job_id`` at once, or after ``wait_seconds``
(capped at 280 s, under Hermes' tool timeout). Poll with ``shortrizz_status``.
Importing this module does not import OpenCV or faster-whisper; the job process does.
"""
from __future__ import annotations

import json, os, re, signal, subprocess, sys, time, uuid
from pathlib import Path

SKILL_ID = "shortrizz:shortrizz-clipper"
TOOLSET = "shortrizz"
MAX_WAIT = 280
PKG_ROOT = Path(__file__).resolve().parent.parent
STYLES = ("hormozi", "mrbeast", "abdaal")
REFRAMES = ("auto", "face", "center", "letterbox")
PLATFORMS = ("youtube", "tiktok", "instagram")


def _home() -> Path:
    return Path(os.environ.get("SHORTRIZZ_HOME", Path.home() / ".cache" / "shortrizz")).expanduser()


def _jobs_dir() -> Path:
    d = _home() / "jobs"; d.mkdir(parents=True, exist_ok=True); return d


def _runs_dir() -> Path:
    d = Path(os.environ.get("SHORTRIZZ_RUNS", Path.home() / "shortrizz-runs")).expanduser()
    d.mkdir(parents=True, exist_ok=True); return d


def _args(params) -> dict:
    if params is None:
        return {}
    if isinstance(params, str):
        params = json.loads(params) if params.strip() else {}
    if not isinstance(params, dict):
        raise ValueError("tool arguments must be an object")
    return params


def _out(payload: dict) -> str:
    payload.setdefault("skill_id", SKILL_ID)
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _err(msg: str, fix: str = "") -> str:
    return _out({"ok": False, "error": msg, "fix": fix or "Correct that argument and call the tool again."})


def _num(a, key, default, lo, hi, cast=float):
    v = a.get(key, default)
    try:
        v = cast(v)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number")
    if not lo <= v <= hi:
        raise ValueError(f"{key} must be between {lo} and {hi}")
    return v


def _check_style(style: str) -> str:
    style = str(style or "hormozi")
    if style not in STYLES and not (style.endswith(".json") and Path(style).expanduser().is_file()):
        raise ValueError(f"style must be one of {', '.join(STYLES)} or an existing .json file")
    return style


# ------------------------------------------------------------------ jobs
def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # zombie check (Linux)
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return True


def _job_python() -> str:
    """Interpreter for the job process: the venv that holds faster-whisper (the plugin env), else ours.

    Hermes may run plugins in its core interpreter with the plugin venv added to sys.path, so
    sys.executable alone would not see the plugin's dependencies.
    """
    import importlib.util
    spec = importlib.util.find_spec("faster_whisper")
    if spec and spec.origin:
        site = Path(spec.origin).resolve().parents[1]            # .../lib/pythonX.Y/site-packages
        for cand in (site.parents[2] / "bin" / "python", site.parents[2] / "bin" / "python3"):
            if cand.exists():
                return str(cand)
    return sys.executable


def _start_job(kind: str, argv: list[str], out_dir: Path) -> dict:
    job_id = f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / "job.log"
    paths = [str(PKG_ROOT)] + [p for p in sys.path if p and Path(p).is_dir()]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(dict.fromkeys(paths)), PYTHONUNBUFFERED="1")
    env.pop("PYTHONHOME", None)
    py = _job_python()
    with open(log, "wb") as fh:
        proc = subprocess.Popen([py, "-m", "shortrizz", *argv], stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, cwd=str(out_dir), env=env, start_new_session=True)
    job = {"job_id": job_id, "kind": kind, "pid": proc.pid, "out_dir": str(out_dir), "log": str(log),
           "started": time.time(), "argv": argv, "python": py}
    (_jobs_dir() / f"{job_id}.json").write_text(json.dumps(job))
    return job


def _load_job(job_id: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", job_id or ""):
        raise ValueError("job_id is not valid")
    p = _jobs_dir() / f"{job_id}.json"
    if not p.exists():
        raise ValueError(f"unknown job_id {job_id}")
    return json.loads(p.read_text())


def _job_state(job: dict) -> dict:
    out = Path(job["out_dir"])
    prog = {}
    try:
        prog = json.loads((out / "progress.json").read_text())
    except (OSError, ValueError):
        pass
    alive = _pid_alive(job["pid"])
    state = {"job_id": job["job_id"], "kind": job["kind"], "out_dir": str(out),
             "elapsed_sec": round(time.time() - job["started"], 1), "stage": prog.get("stage", "starting"),
             "progress": {"done": prog.get("done", 0), "total": prog.get("total", 0)}}
    if prog.get("stage") == "done" and (out / "clips.json").exists():
        from shortrizz.pipeline import summary
        state.update(status="done", ok=True, result=summary(json.loads((out / "clips.json").read_text()), out),
                     next_step="Show the user the clip files and gallery. Offer shortrizz_render_style or "
                               "shortrizz_publish_plan (dry run). Do not post anywhere.")
    elif alive:
        state.update(status="running", ok=True,
                     next_step=f"Call shortrizz_status with job_id={job['job_id']} and wait_seconds up to {MAX_WAIT}.")
    else:
        tail = ""
        try:
            tail = "\n".join(Path(job["log"]).read_text(errors="replace").splitlines()[-12:])
        except OSError:
            pass
        state.update(status="failed", ok=False, error="job exited before finishing", log_tail=tail,
                     next_step="Read log_tail, fix the input (URL, file, style) and start again.")
    return state


def _wait(job: dict, wait: float) -> dict:
    end = time.time() + wait
    st = _job_state(job)
    while st["status"] == "running" and time.time() < end:
        time.sleep(min(3, max(0.2, end - time.time())))
        st = _job_state(job)
    return st


# ------------------------------------------------------------------ handlers
def make_shorts_tool(params=None, **_kw) -> str:
    try:
        a = _args(params)
        source = str(a.get("source") or "").strip()
        if not source:
            raise ValueError("source (a video URL or local file path) is required")
        if not re.match(r"^https?://", source):
            p = Path(source).expanduser()
            if not p.is_file():
                raise ValueError(f"local file not found: {source}")
            source = str(p.resolve())
        count = _num(a, "count", 5, 1, 20, int)
        mn = _num(a, "min_seconds", 30, 5, 180); mx = _num(a, "max_seconds", 60, 5, 180)
        if mn > mx:
            raise ValueError("min_seconds must be <= max_seconds")
        style = _check_style(a.get("style"))
        reframe = str(a.get("reframe") or "auto")
        if reframe not in REFRAMES:
            raise ValueError(f"reframe must be one of {', '.join(REFRAMES)}")
        model = str(a.get("model") or "small")
        if model not in ("tiny", "base", "small", "medium", "large-v3", "distil-large-v3"):
            raise ValueError("model must be tiny, base, small, medium, large-v3 or distil-large-v3")
        wait = _num(a, "wait_seconds", 0, 0, MAX_WAIT)
        out = Path(a["out_dir"]).expanduser() if a.get("out_dir") else None
    except ValueError as e:
        return _err(str(e))
    argv = [source, "--count", str(count), "--min", f"{mn:g}", "--max", f"{mx:g}", "--style", style,
            "--reframe", reframe, "--model", model, "--json"]
    if a.get("use_llm") is False:
        argv.append("--no-llm")
    if a.get("cache_dir"):
        argv += ["--cache", str(Path(a["cache_dir"]).expanduser())]
    out = (out or _runs_dir() / f"run-{time.strftime('%Y%m%d-%H%M%S')}").resolve()
    argv += ["--out", str(out)]
    job = _start_job("make", argv, out)
    st = _wait(job, wait)
    st["note"] = ("First run on a new video downloads and transcribes it (about 1/10 of its length on 8 CPU "
                  "cores), then renders ~25 s per clip. Cached videos only re-render.")
    return _out(st)


def status_tool(params=None, **_kw) -> str:
    try:
        a = _args(params)
        job = _load_job(str(a.get("job_id") or ""))
        wait = _num(a, "wait_seconds", 0, 0, MAX_WAIT)
    except ValueError as e:
        return _err(str(e), "Pass the job_id returned by shortrizz_make_shorts or shortrizz_render_style.")
    if a.get("cancel"):
        try:
            os.killpg(job["pid"], signal.SIGTERM)
        except OSError:
            pass
        return _out({"ok": True, "job_id": job["job_id"], "status": "cancelled", "next_step": "Job stopped."})
    return _out(_wait(job, wait))


def list_styles_tool(params=None, **_kw) -> str:
    from shortrizz.captions import describe_styles
    return _out({"ok": True, **describe_styles(), "reframe_modes": list(REFRAMES),
                 "next_step": "Pass one of these names as style to shortrizz_make_shorts or shortrizz_render_style."})


def render_style_tool(params=None, **_kw) -> str:
    try:
        a = _args(params)
        run = Path(str(a.get("run_dir") or "")).expanduser()
        if not (run / "clips.json").is_file():
            raise ValueError("run_dir must be a finished Shortrizz output folder containing clips.json")
        style = _check_style(a.get("style"))
        wait = _num(a, "wait_seconds", 0, 0, MAX_WAIT)
        reframe = a.get("reframe")
        if reframe and reframe not in REFRAMES:
            raise ValueError(f"reframe must be one of {', '.join(REFRAMES)}")
    except ValueError as e:
        return _err(str(e))
    out = Path(a["out_dir"]).expanduser().resolve() if a.get("out_dir") else run.resolve().parent / f"{run.name}-{Path(style).stem}"
    argv = ["restyle", str(run.resolve()), "--style", style, "--out", str(out)] + (["--reframe", reframe] if reframe else [])
    st = _wait(_start_job("restyle", argv, out), wait)
    return _out(st)


def publish_plan_tool(params=None, **_kw) -> str:
    try:
        a = _args(params)
        run = Path(str(a.get("run_dir") or "")).expanduser()
        if not (run / "clips.json").is_file():
            raise ValueError("run_dir must contain clips.json")
        plats = a.get("platforms") or list(PLATFORMS)
        if isinstance(plats, str):
            plats = [p.strip() for p in plats.split(",") if p.strip()]
        bad = [p for p in plats if p not in PLATFORMS]
        if bad:
            raise ValueError(f"unknown platform(s) {bad}; use {', '.join(PLATFORMS)}")
        every = _num(a, "every_hours", 24, 0.25, 24 * 14)
    except ValueError as e:
        return _err(str(e))
    from shortrizz import publish
    posts = publish.plan(run, plats, every_hours=every, live=False)
    return _out({"ok": True, "dry_run": True, "plan_file": str(run / "publish_plan.json"), "posts": posts,
                 "next_step": "This is a plan only. Nothing was uploaded; live posting is not implemented."})


# ------------------------------------------------------------------ schemas
def _schema(name, desc, props, required):
    return {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required}}

_WAIT = {"type": "integer", "minimum": 0, "maximum": MAX_WAIT,
         "description": f"Block up to this many seconds for the job to finish (0-{MAX_WAIT}). Default 0."}


def tool_specs() -> list[dict]:
    style = {"type": "string", "description": "hormozi | mrbeast | abdaal, or a path to a custom style .json"}
    specs = [
        ("shortrizz_make_shorts", make_shorts_tool, "🎬",
         "Turn a long video (YouTube/any yt-dlp URL or a local file) into captioned 9:16 shorts: transcribe, "
         "pick the best moments with an LLM, face-track reframe, burn word-by-word captions. Starts a "
         "background job and returns job_id; poll shortrizz_status. Only for content the user has rights to.",
         {"source": {"type": "string", "description": "Video URL or local file path."},
          "count": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Clips to make. Default 5."},
          "min_seconds": {"type": "number", "description": "Shortest clip. Default 30."},
          "max_seconds": {"type": "number", "description": "Longest clip. Default 60."},
          "style": style,
          "reframe": {"type": "string", "enum": list(REFRAMES), "description": "Default auto (face tracking)."},
          "model": {"type": "string", "description": "faster-whisper size. Default small."},
          "use_llm": {"type": "boolean", "description": "false = heuristic picks only. Default true."},
          "out_dir": {"type": "string", "description": "Output folder. Default ~/shortrizz-runs/run-<time>."},
          "cache_dir": {"type": "string", "description": "Download/transcript cache. Default ~/.cache/shortrizz."},
          "wait_seconds": _WAIT}, ["source"]),
        ("shortrizz_status", status_tool, "⏳",
         "Progress or result of a Shortrizz job. When done returns clip paths, titles, virality scores, "
         "durations, why-it-hooks and captions with hashtags.",
         {"job_id": {"type": "string"}, "wait_seconds": _WAIT,
          "cancel": {"type": "boolean", "description": "Stop the job."}}, ["job_id"]),
        ("shortrizz_list_styles", list_styles_tool, "🎨", "List caption style presets and reframe modes.", {}, []),
        ("shortrizz_render_style", render_style_tool, "🖌️",
         "Re-render a finished run's clips with another caption style (no download, transcription or LLM). "
         "Background job; returns job_id.",
         {"run_dir": {"type": "string", "description": "Folder of a finished run (has clips.json)."},
          "style": style, "reframe": {"type": "string", "enum": list(REFRAMES)},
          "out_dir": {"type": "string"}, "wait_seconds": _WAIT}, ["run_dir", "style"]),
        ("shortrizz_publish_plan", publish_plan_tool, "🗓️",
         "Dry run: write publish_plan.json scheduling a run's clips to YouTube Shorts / TikTok / Instagram "
         "Reels. Never uploads.",
         {"run_dir": {"type": "string"},
          "platforms": {"type": "array", "items": {"type": "string", "enum": list(PLATFORMS)}},
          "every_hours": {"type": "number", "description": "Gap between posts. Default 24."}}, ["run_dir"]),
    ]
    return [{"name": n, "handler": h, "emoji": e, "description": d, "schema": _schema(n, d, p, r)}
            for n, h, e, d, p, r in specs]


TOOL_NAMES = tuple(s["name"] for s in tool_specs())


def register_tools(ctx) -> None:
    for s in tool_specs():
        ctx.register_tool(name=s["name"], toolset=TOOLSET, schema=s["schema"], handler=s["handler"],
                          description=s["description"], emoji=s["emoji"])
