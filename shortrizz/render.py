import json, subprocess
from pathlib import Path
from .util import run, log
from . import reframe

def probe(src):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=width,height,r_frame_rate", "-of", "json", str(src)], capture_output=True, text=True).stdout
    s = json.loads(out)["streams"][0]
    n, d = s["r_frame_rate"].split("/")
    return s["width"], s["height"], float(n) / float(d)

def render_clip(src, start, end, ass_path, out_path, reframe_mode="auto", crf=20, preset="medium"):
    sw, sh, fps = probe(src)
    fps = min(fps, 60.0)
    dur = end - start
    plan = reframe.make_plan(src, start, dur, sw, sh, reframe_mode)
    ass = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    enc = subprocess.Popen([
        "ffmpeg", "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "1080x1920", "-r", f"{fps}", "-i", "-",
        "-ss", f"{start}", "-t", f"{dur}", "-i", str(src),
        "-map", "0:v", "-map", "1:a?", "-vf", f"ass='{ass}'",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-movflags", "+faststart", "-shortest", str(out_path)], stdin=subprocess.PIPE)
    n = 0
    for fr in reframe.render_frames(src, start, dur, sw, sh, fps, analysis=plan):
        enc.stdin.write(fr.tobytes()); n += 1
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError(f"ffmpeg encode failed for {out_path}")
    return {"frames": n, "reframe": "letterbox" if plan is None else ("center" if len(plan[0]) == 1 else "face")}

def thumbnail(mp4, out_jpg, t=1.0):
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t}", "-i", str(mp4), "-frames:v", "1", "-vf", "scale=360:-2",
         "-q:v", "3", str(out_jpg)])
