import json, re
from pathlib import Path
from .util import run, which, log

def is_url(s):
    return bool(re.match(r"^https?://", s))

def fetch(src, cache_dir):
    """Return (video_path, meta dict). Downloads URLs with yt-dlp (cached by video id)."""
    if not is_url(src):
        p = Path(src).expanduser().resolve()
        if not p.exists():
            raise SystemExit(f"file not found: {src}")
        return p, {"title": p.stem, "id": p.stem, "source": str(p)}
    ytdlp = which("yt-dlp")
    if not ytdlp:
        raise SystemExit("yt-dlp not found (install to ~/.local/bin or PATH)")
    cache_dir = Path(cache_dir); cache_dir.mkdir(parents=True, exist_ok=True)
    vid = run([ytdlp, "--print", "id", "--skip-download", "--no-warnings", src],
              capture_output=True, text=True).stdout.strip().splitlines()[-1]
    out = cache_dir / f"{vid}.mp4"
    info = cache_dir / f"{vid}.info.json"
    if not out.exists():
        log(f"downloading {src}")
        cmd = [ytdlp, "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
               "--merge-output-format", "mp4", "--write-info-json", "--no-playlist",
               "-o", str(cache_dir / "%(id)s.%(ext)s"), src]
        if which("deno"):
            cmd[1:1] = ["--js-runtimes", f"deno:{which('deno')}", "--remote-components", "ejs:github"]
        try:
            run(cmd)
        except Exception:
            log("retrying yt-dlp without JS runtime options")
            run([c for c in cmd if not c.startswith(("--js-runtimes", "deno:", "--remote-components", "ejs:"))])
    else:
        log(f"using cached download {out}")
    meta = {"id": vid, "source": src, "title": vid}
    if info.exists():
        d = json.loads(info.read_text())
        meta.update(title=d.get("title", vid), channel=d.get("channel"), license=d.get("license"),
                    webpage_url=d.get("webpage_url"))
    return out, meta
