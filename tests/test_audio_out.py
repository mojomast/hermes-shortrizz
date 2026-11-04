import shutil, subprocess
import pytest
from shortrizz import render as R

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

def _src(tmp_path):
    p = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x240:d=10",
                    "-f", "lavfi", "-i", "sine=f=440:d=10", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(p)],
                   check=True)
    return p

def _encode(tmp_path, src, items, sfx=()):
    base = 0.0
    nseg = max(1, sum(1 for it in items if it["kind"] == "src"))
    sin = ["-ss", "0", "-t", "10", "-i", str(src)] * nseg
    inputs, chains, lab = R._audio_graph(items, 15, list(sfx), True, 0, base)
    out = tmp_path / "out.mp4"
    cmd = ["ffmpeg", "-v", "error", "-y", *sin]
    for x in inputs:
        cmd += ["-i", str(x)]
    cmd += ["-filter_complex", ";".join(chains), "-map", f"[{lab}]", "-c:a", "aac", str(out)]
    subprocess.run(cmd, check=True)
    return out

def test_cold_open_before_body_keeps_audio(tmp_path):
    # a later segment (the cold open) played before an earlier one used to come out silent / with no audio
    src = _src(tmp_path)
    items = [{"kind": "src", "s": 6.0, "e": 8.5}, {"kind": "src", "s": 1.0, "e": 5.0}]
    out = _encode(tmp_path, src, items)
    assert R.check_audio(out) > -40

def test_holds_and_sfx_keep_audio(tmp_path):
    src = _src(tmp_path)
    sfx = R.__file__.replace("render.py", "assets/sfx/pop.wav")
    items = [{"kind": "hold", "dur": 1.0}, {"kind": "src", "s": 6.0, "e": 8.0},
             {"kind": "src", "s": 1.0, "e": 4.0}, {"kind": "hold", "dur": 0.8}]
    out = _encode(tmp_path, src, items, sfx=[(0.5, sfx), (3.0, sfx)])
    assert R.check_audio(out) > -40

def test_check_audio_rejects_missing_and_silent(tmp_path):
    v = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
                    "-c:v", "libx264", str(v)], check=True)
    with pytest.raises(RuntimeError, match="no audio stream"):
        R.check_audio(v)
    s = tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                    "-t", "2", "-c:a", "aac", str(s)], check=True)
    with pytest.raises(RuntimeError, match="silent"):
        R.check_audio(s)
