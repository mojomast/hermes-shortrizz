"""Hermes tools for Shortrizz.

Rendering takes minutes, so make/render tools start a background job (a detached
``python -m shortrizz`` process) and return a ``job_id`` at once, or after ``wait_seconds``
(capped at 280 s, under Hermes' tool timeout). Poll with ``shortrizz_status``.
Importing this module does not import OpenCV or faster-whisper; the job process does.
"""
from __future__ import annotations

import json, os, re, signal, subprocess, sys, time, uuid
from pathlib import Path

from .secrets import DECLARED_SECRETS, get_secret

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


# Exact names copied into a job. LANG/LC_* and SHORTRIZZ_* are added below.
# TLS CA paths are not secrets; dropping them breaks HTTPS downloads.
_CHILD_EXACT = frozenset({
    "PATH", "HOME", "TMPDIR", "LANG",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
})


def runs_root() -> Path:
    return Path(os.environ.get("SHORTRIZZ_RUNS", Path.home() / "shortrizz-runs")).expanduser().resolve()


def cache_root() -> Path:
    raw = os.environ.get("SHORTRIZZ_CACHE") or str(Path.home() / ".cache" / "shortrizz")
    return Path(raw).expanduser().resolve()


def confine_path(path, root: Path, label: str) -> Path:
    """Resolve ``path`` and refuse anything outside ``root``, including ``..`` and symlink escapes."""
    root = Path(root).expanduser().resolve()
    if not str(path).strip():
        raise ValueError(f"{label} is empty. Choose a path inside {root}.")
    resolved = Path(path).expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(
            f"{label} must stay inside {root}. "
            f"{path} resolves to {resolved}, which is outside that folder."
        ) from None
    return resolved


def child_env(pythonpath: str) -> dict:
    """Environment for a background job. Undeclared provider keys never cross into it."""
    env = {}
    for key, value in os.environ.items():
        if key in _CHILD_EXACT or key.startswith("LC_"):
            env[key] = value
        elif key.startswith("SHORTRIZZ_") and not key.endswith("_ENV_FILE"):
            env[key] = value
    for name in DECLARED_SECRETS:
        value = get_secret(name)
        if value:
            env[name] = value
    env["PYTHONPATH"] = pythonpath
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("PYTHONHOME", None)
    return env


def _stage_of(a):
    stage = str(a.get("stage") or "all")
    if stage not in ("all", "select", "render"):
        raise ValueError('stage must be "all", "select" or "render"')
    picks = None
    if stage == "render":
        if not str(a.get("picks_path") or "").strip():
            raise ValueError("picks_path is required when stage is render")
        picks = confine_path(a["picks_path"], runs_root(), "picks_path")
        if not picks.is_file():
            raise ValueError(f"picks file not found: {picks}")
    elif a.get("picks_path"):
        raise ValueError("picks_path is only used when stage is render")
    return stage, picks


def _confined_out(a, default: Path) -> Path:
    if a.get("out_dir"):
        return confine_path(a["out_dir"], runs_root(), "out_dir")
    return confine_path(default, runs_root(), "out_dir")


def _confined_cache(a):
    if not a.get("cache_dir"):
        return None
    return confine_path(a["cache_dir"], cache_root(), "cache_dir")


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
    env = child_env(os.pathsep.join(dict.fromkeys(paths)))
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
    if prog.get("stage") == "picks_ready" and (out / "picks.json").is_file():
        try:
            from shortrizz.picks import load_picks, summaries
            doc = load_picks(out / "picks.json")
        except ValueError as e:
            state.update(status="failed", ok=False, stage="picks_ready", error=str(e),
                         next_step="Fix picks.json (see docs/PICKS_CONTRACT.md) and call the make tool with stage \"render\".")
            return state
        state["stage"] = "picks_ready"
        state.update(status="done", ok=True, picks_kind=doc.get("kind"), run_id=doc.get("run_id"),
                     picks_json=str(out / "picks.json"), picks=summaries(doc, out),
                     next_step=("Picks are ready and nothing has been encoded. Show each pick's title, score, "
                                "hook, start/end and still. After edits, call the same make tool with "
                                "stage \"render\" and picks_path set to picks.json."))
        return state
    if prog.get("stage") == "done" and (out / "clips.json").exists():
        from shortrizz.pipeline import summary
        doc = json.loads((out / "clips.json").read_text())
        if doc.get("format") == "highlights":
            nxt = ("Show the user the 16:9 highlights file, its duration, and each segment's title, "
                   "virality score and source time. chapters.txt and highlights.json sit beside the video. "
                   "Do not post it anywhere.")
        else:
            nxt = ("Show the user the clip files and gallery. Offer shortrizz_render_style or "
                   "shortrizz_publish_plan (dry run). Do not post anywhere.")
        state.update(status="done", ok=True, result=summary(doc, out), next_step=nxt)
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
def _watermark_argv(a):
    if a.get("watermark") is True:
        return ["--watermark"]
    if a.get("watermark") is False:
        return ["--no-watermark"]
    return []


def make_shorts_tool(params=None, **_kw) -> str:
    try:
        a = _args(params)
        stage, picks = _stage_of(a)
        wait = _num(a, "wait_seconds", 0, 0, MAX_WAIT)
        reframe = a.get("reframe")
        if reframe and reframe not in REFRAMES:
            raise ValueError(f"reframe must be one of {', '.join(REFRAMES)}")
        if stage == "render":
            out = _confined_out(a, picks.parent)
        else:
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
            reframe = str(reframe or "auto")
            model = str(a.get("model") or "small")
            if model not in ("tiny", "base", "small", "medium", "large-v3", "distil-large-v3"):
                raise ValueError("model must be tiny, base, small, medium, large-v3 or distil-large-v3")
            cache = _confined_cache(a)
            out = _confined_out(a, _runs_dir() / f"run-{time.strftime('%Y%m%d-%H%M%S')}")
    except ValueError as e:
        return _err(str(e))
    try:
        edits = _edit_argv(a)
    except ValueError as e:
        return _err(str(e))
    if stage == "render":
        argv = ["--picks", str(picks), "--json", "--out", str(out), *edits, *_watermark_argv(a)]
        if a.get("reframe"):
            argv += ["--reframe", a["reframe"]]
        note = "Rendering the enabled picks in order, with each pick's trim, title and caption style."
    else:
        argv = [source, "--count", str(count), "--min", f"{mn:g}", "--max", f"{mx:g}", "--style", style,
                "--reframe", reframe, "--model", model, "--json", *edits, *_watermark_argv(a)]
        if a.get("use_llm") is False:
            argv.append("--no-llm")
        if a.get("length") == "tight":
            argv += ["--length", "tight"]
        if a.get("avoid_previous"):
            argv.append("--avoid-previous")
        if stage == "select":
            argv.append("--select-only")
        if cache is not None:
            argv += ["--cache", str(cache)]
        argv += ["--out", str(out)]
        note = ("Selection only: writes picks.json and a still per pick, then stops before any encode. "
                "Poll until stage is picks_ready." if stage == "select" else
                "First run on a new video downloads and transcribes it (about 1/10-1/4 of its length on 8 CPU "
                "cores), then renders about 1 min per clip with edits. Cached videos only re-render.")
    st = _wait(_start_job("make", argv, out), wait)
    st["note"] = note
    return _out(st)


EDIT_BOOLS = {"deeper_edits": ("--no-edits", None), "llm_edits": ("--no-llm-edits", None),
              "hook_text": ("--no-hook-text", None), "loudnorm": ("--no-loudnorm", None),
              "cold_open": (None, "--cold-open"), "tts_intro": (None, "--tts-intro"), "tts_outro": (None, "--tts-outro"),
              "zoom": (None, "--zoom"), "loop": (None, "--loop"), "emoji": (None, "--emoji"), "sfx": (None, "--sfx")}

def _edit_argv(a):
    argv = []
    if a.get("edit") is not None:
        if a["edit"] not in ("off", "light", "normal", "aggressive"):
            raise ValueError("edit must be off, light, normal or aggressive")
        argv += ["--edit", a["edit"]]
    for k, (off, on) in EDIT_BOOLS.items():
        if a.get(k) is False and off:
            argv.append(off)
        if a.get(k) is True and on:
            argv.append(on)
    return argv

_EDIT_PARAMS = {
    "edit": {"type": "string", "enum": ["off", "light", "normal", "aggressive"],
             "description": "Dead-air tightening preset. Default normal (gaps >0.35 s shrink to 0.15 s)."},
    "deeper_edits": {"type": "boolean", "description": "false = plain continuous render (no cuts, hook, loudnorm). Default true."},
    "llm_edits": {"type": "boolean", "description": "Second LLM pass removing false starts/repeats/fillers. Default true."},
    "hook_text": {"type": "boolean", "description": "On-screen hook text in the first 3 s. Default true."},
    "loudnorm": {"type": "boolean", "description": "Two-pass loudness normalise to -14 LUFS. Default true."},
    "cold_open": {"type": "boolean", "description": "Opt-in: flash the clip's best line first. Default false."},
    "tts_intro": {"type": "boolean", "description": "Opt-in: AI narrator (Kokoro, stock voice) context line first. Default false."},
    "tts_outro": {"type": "boolean", "description": "Opt-in: AI narrator call to action at the end. Default false."},
    "zoom": {"type": "boolean", "description": "Opt-in: punch-in zooms on key words and on jump cuts. Default false."},
    "loop": {"type": "boolean", "description": "Opt-in: seamless loop ending. Default false."},
    "emoji": {"type": "boolean", "description": "Opt-in: emoji pops on key words. Default false."},
    "sfx": {"type": "boolean", "description": "Opt-in: subtle pop/whoosh sound effects (bundled CC0). Default false."},
}


def status_tool(params=None, **_kw) -> str:
    try:
        a = _args(params)
        job = _load_job(str(a.get("job_id") or ""))
        wait = _num(a, "wait_seconds", 0, 0, MAX_WAIT)
    except ValueError as e:
        return _err(str(e), "Pass the job_id returned by shortrizz_make_shorts, shortrizz_make_highlights or shortrizz_render_style.")
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
                 "next_step": "Pass one of these names as style to shortrizz_make_shorts, "
                              "shortrizz_make_highlights or shortrizz_render_style."})


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
        default_out = run.resolve().parent / f"{run.name}-{Path(style).stem}"
        out = _confined_out(a, default_out)
    except ValueError as e:
        return _err(str(e))
    argv = ["restyle", str(run.resolve()), "--style", style, "--out", str(out)] + (["--reframe", reframe] if reframe else [])
    if a.get("watermark") is True:
        argv.append("--watermark")
    elif a.get("watermark") is False:
        argv.append("--no-watermark")
    try:
        argv += _edit_argv(a)
    except ValueError as e:
        return _err(str(e))
    st = _wait(_start_job("restyle", argv, out), wait)
    return _out(st)


def make_highlights_tool(params=None, **_kw) -> str:
    """One 16:9 highlights reel. Same scoring as shorts; background job, poll shortrizz_status."""
    try:
        a = _args(params)
        stage, picks = _stage_of(a)
        wait = _num(a, "wait_seconds", 0, 0, MAX_WAIT)
        if stage == "render":
            out = _confined_out(a, picks.parent)
        else:
            source = str(a.get("source") or "").strip()
            if not source:
                raise ValueError("source (a video URL or local file path) is required")
            if not re.match(r"^https?://", source):
                p = Path(source).expanduser()
                if not p.is_file():
                    raise ValueError(f"local file not found: {source}")
                source = str(p.resolve())
            duration = _num(a, "duration", 180, 0, 14400)
            max_clips = _num(a, "max_clips", 8, 0, 40, int)
            min_score = _num(a, "min_score", 50, 0, 100)
            mn = _num(a, "min_seconds", 15, 5, 300)
            mx = _num(a, "max_seconds", 45, 5, 600)
            if mn > mx:
                raise ValueError("min_seconds must be <= max_seconds")
            order = str(a.get("order") or "chrono")
            if order not in ("chrono", "score"):
                raise ValueError("order must be chrono or score")
            fade = _num(a, "crossfade", 0.4, 0, 2)
            style = str(a.get("style") or "hormozi")
            if a.get("captions") is not False:
                style = _check_style(style)
            model = str(a.get("model") or "small")
            if model not in ("tiny", "base", "small", "medium", "large-v3", "distil-large-v3"):
                raise ValueError("model must be tiny, base, small, medium, large-v3 or distil-large-v3")
            cache = _confined_cache(a)
            out = _confined_out(a, _runs_dir() / f"highlights-{time.strftime('%Y%m%d-%H%M%S')}")
    except ValueError as e:
        return _err(str(e))
    if stage == "render":
        argv = ["highlights", "--picks", str(picks), "--json", "--out", str(out), *_watermark_argv(a)]
        note = "Rendering the enabled highlight picks in order, with each pick's trim, title and caption style."
    else:
        argv = ["highlights", source, "--duration", f"{duration:g}", "--max-clips", str(max_clips),
                "--min-score", f"{min_score:g}", "--min", f"{mn:g}", "--max", f"{mx:g}",
                "--order", order, "--crossfade", f"{fade:g}", "--style", style, "--model", model, "--json",
                *_watermark_argv(a)]
        if a.get("hard_cuts"):
            argv.append("--hard-cuts")
        if a.get("captions") is False:
            argv.append("--no-captions")
        if a.get("loudnorm") is False:
            argv.append("--no-loudnorm")
        if a.get("chapters") is False:
            argv.append("--no-chapters")
        if a.get("manifest") is False:
            argv.append("--no-manifest")
        if a.get("use_llm") is False:
            argv.append("--no-llm")
        if a.get("avoid_previous"):
            argv.append("--avoid-previous")
        if stage == "select":
            argv.append("--select-only")
        if cache is not None:
            argv += ["--cache", str(cache)]
        argv += ["--out", str(out)]
        note = ("Selection only: writes picks.json and a still per pick, then stops before any encode. "
                "Poll until stage is picks_ready." if stage == "select" else
                "Highlights uses the same download, transcript and virality scores as shorts, then joins the "
                "top segments into one 16:9 file. The first run transcribes the source; rendering is one "
                "encode per segment plus the join.")
    st = _wait(_start_job("highlights", argv, out), wait)
    st["note"] = note
    return _out(st)


def publish_plan_tool(params=None, **_kw) -> str:
    try:
        a = _args(params)
        run = Path(str(a.get("run_dir") or "")).expanduser()
        if not (run / "clips.json").is_file():
            raise ValueError("run_dir must contain clips.json")
        kind = json.loads((run / "clips.json").read_text()).get("format")
        if kind == "highlights":
            raise ValueError("this run is a 16:9 highlights video, not a shorts feed")
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


def _brief_edits():
    """Same edit knobs as make_shorts, with short descriptions so the schema is not copied twice."""
    short = {
        "edit": "Dead-air preset.",
        "deeper_edits": "false = plain render.",
        "llm_edits": "LLM filler pass.",
        "hook_text": "On-screen hook.",
        "loudnorm": "Normalise to -14 LUFS.",
        "cold_open": "Opt-in cold open.",
        "tts_intro": "Opt-in narrator intro.",
        "tts_outro": "Opt-in narrator outro.",
        "zoom": "Opt-in punch-in.",
        "loop": "Opt-in loop ending.",
        "emoji": "Opt-in emoji.",
        "sfx": "Opt-in pop/whoosh.",
    }
    out = {}
    for key, spec in _EDIT_PARAMS.items():
        item = {"type": spec["type"], "description": short[key]}
        if "enum" in spec:
            item["enum"] = spec["enum"]
        out[key] = item
    return out


_STAGE = {"type": "string", "enum": ["all", "select", "render"],
          "description": "all (default) selects and renders. select writes picks.json and stills, no encode. render encodes picks_path."}
_PICKS = {"type": "string", "description": "picks.json to encode when stage is render. Enabled picks, in order."}
_OUT = {"type": "string", "description": "Output folder inside ~/shortrizz-runs (or $SHORTRIZZ_RUNS)."}
_CACHE = {"type": "string", "description": "Cache inside ~/.cache/shortrizz (or $SHORTRIZZ_CACHE)."}


def tool_specs() -> list[dict]:
    style = {"type": "string", "description": "hormozi | mrbeast | abdaal, or a path to a custom style .json"}
    specs = [
        ("shortrizz_make_shorts", make_shorts_tool, "🎬",
         "Turn a long video (YouTube/any yt-dlp URL or a local file) into captioned 9:16 shorts: transcribe, "
         "pick the best moments with an LLM, face-track reframe, burn word-by-word captions. "
         "stage=select stops after picks.json and stills; stage=render encodes picks_path. "
         "Starts a background job and returns job_id; poll shortrizz_status. Only for content the user has rights to.",
         {"source": {"type": "string", "description": "Video URL or local file path."},
          "count": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Clips to make. Default 5."},
          "min_seconds": {"type": "number", "description": "Shortest clip. Default 30."},
          "max_seconds": {"type": "number", "description": "Longest clip. Default 60."},
          "style": style,
          "reframe": {"type": "string", "enum": list(REFRAMES), "description": "Default auto (face tracking)."},
          "model": {"type": "string", "description": "faster-whisper size. Default small."},
          "use_llm": {"type": "boolean", "description": "false = heuristic picks only. Default true."},
          "stage": _STAGE, "picks_path": _PICKS,
          "out_dir": _OUT,
          "watermark": {"type": "boolean", "description": "Burn in the small Shortrizz logo watermark. Omit to use the user's setting (off by default)."},
          "cache_dir": _CACHE,
          "length": {"type": "string", "enum": ["default", "tight"], "description": "tight = 20-35 s clips."},
          "avoid_previous": {"type": "boolean", "description": "Skip moments earlier runs on this video already used."},
          **_EDIT_PARAMS, "wait_seconds": _WAIT}, ["source"]),
        ("shortrizz_make_highlights", make_highlights_tool, "🎞️",
         "Build ONE 16:9 highlights video from the highest-scoring moments of a long video (same transcript "
         "and virality scores as shorts, no vertical crop). Target duration, max segment count and minimum "
         "score; chronological order by default. Short audio and video crossfade, optional lower-third "
         "captions. stage=select stops after picks.json and stills; stage=render encodes picks_path. "
         "Starts a background job and returns job_id; poll shortrizz_status. Only for content the user has rights to.",
         {"source": {"type": "string", "description": "Video URL or local file path."},
          "duration": {"type": "number", "description": "Target total length in seconds. Default 180. 0 = no duration cap."},
          "max_clips": {"type": "integer", "minimum": 0, "maximum": 40, "description": "Max segments. Default 8. 0 = no count cap."},
          "min_score": {"type": "number", "description": "Drop segments scored below this (0-100). Default 50."},
          "min_seconds": {"type": "number", "description": "Shortest segment. Default 15."},
          "max_seconds": {"type": "number", "description": "Longest segment. Default 45."},
          "order": {"type": "string", "enum": ["chrono", "score"], "description": "chrono (default) keeps story order; score plays the strongest first."},
          "crossfade": {"type": "number", "description": "Audio and video crossfade in seconds. Default 0.4. 0 = hard cuts."},
          "hard_cuts": {"type": "boolean", "description": "Join with hard cuts instead of a crossfade. Default false."},
          "captions": {"type": "boolean", "description": "Burn lower-third captions. Default true."},
          "loudnorm": {"type": "boolean", "description": "Normalise each segment to -14 LUFS. Default true."},
          "chapters": {"type": "boolean", "description": "Write chapters.txt with source time and score. Default true."},
          "manifest": {"type": "boolean", "description": "Write highlights.json. Default true."},
          "style": style,
          "model": {"type": "string", "description": "faster-whisper size. Default small."},
          "use_llm": {"type": "boolean", "description": "false = heuristic picks only. Default true."},
          "avoid_previous": {"type": "boolean", "description": "Skip moments earlier runs on this video already used."},
          "stage": _STAGE, "picks_path": _PICKS,
          "out_dir": _OUT,
          "watermark": {"type": "boolean", "description": "Burn in the small Shortrizz logo. Omit to use the user's setting (off by default)."},
          "cache_dir": _CACHE,
          "wait_seconds": _WAIT}, ["source"]),
        ("shortrizz_status", status_tool, "⏳",
         "Progress or result of a Shortrizz job. When done, returns each short or the highlights file. "
         "stage picks_ready means picks.json and stills are ready and nothing has been encoded.",
         {"job_id": {"type": "string"}, "wait_seconds": _WAIT,
          "cancel": {"type": "boolean", "description": "Stop the job."}}, ["job_id"]),
        ("shortrizz_list_styles", list_styles_tool, "🎨", "List caption style presets and reframe modes.", {}, []),
        ("shortrizz_render_style", render_style_tool, "🖌️",
         "Re-render a finished run's clips with another caption style (no download, transcription or LLM). "
         "Background job; returns job_id.",
         {"run_dir": {"type": "string", "description": "Folder of a finished run (has clips.json)."},
          "style": style, "reframe": {"type": "string", "enum": list(REFRAMES)},
          "watermark": {"type": "boolean", "description": "Burn in the small Shortrizz logo watermark. Omit to use the user's setting (off by default)."},
          **_brief_edits(), "out_dir": _OUT, "wait_seconds": _WAIT}, ["run_dir", "style"]),
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
