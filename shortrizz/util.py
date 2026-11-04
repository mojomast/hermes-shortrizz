import json, os, subprocess, shutil, sys, time
from pathlib import Path

LOCAL_BIN = Path.home() / ".local" / "bin"

def which(name):
    p = LOCAL_BIN / name
    return str(p) if p.exists() else shutil.which(name)

def run(cmd, **kw):
    kw.setdefault("check", True)
    return subprocess.run([str(c) for c in cmd], **kw)

def log(msg):
    print(f"[shortrizz {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)

def load_env_file(path):
    """Load KEY=VALUE pairs from a dotenv file into os.environ (no overwrite). Never logs values."""
    path = Path(os.path.expanduser(path))
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)

def ffprobe_duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
              capture_output=True, text=True).stdout
    return float(json.loads(out)["format"]["duration"])

def fmt_dur(s):
    return f"{int(s//60)}:{int(s%60):02d}"

def models_dir():
    d = Path(os.environ.get("SHORTRIZZ_MODELS", Path.home() / ".cache" / "shortrizz" / "models")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d
