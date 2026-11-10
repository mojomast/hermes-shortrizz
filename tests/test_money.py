"""Money shot: frame 0 is a finished hero. Fixtures only, no model download and no LLM."""
import json
import shutil
import subprocess

import numpy as np
import pytest

from shortrizz import captions, cli, money, pipeline

pytestmark_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs ffmpeg")


def _grid(h=90, w=160, color=(40, 210, 40), step=8):
    fr = np.zeros((h, w, 3), np.uint8)
    fr[:] = color
    fr[::step, :] = (0, 0, 0)
    fr[:, ::step] = (0, 0, 0)
    return fr


def test_rank_prefers_sharp_lit_frame_over_black_and_blur():
    import cv2
    sharp = _grid()
    black = np.zeros_like(sharp)
    blur = cv2.GaussianBlur(sharp, (0, 0), 11)
    gray = np.full_like(sharp, 30)
    best = money.rank_frames([(0.0, black), (0.25, gray), (0.5, blur), (0.75, sharp), (1.0, blur)])
    assert best["t"] == 0.75
    assert best["luma"] > 40 and best["sharp"] > money._measure(blur, None)["sharp"]


def test_explicit_hero_time_outranks_a_sharper_frame():
    sharp = _grid()
    dull = np.full_like(sharp, 70)
    best = money.rank_frames([(0.0, sharp), (0.5, dull)], hero_time=0.5)
    assert best["t"] == 0.5 and best["pinned"] is True
    flagged = money.rank_frames([(0.0, dull)], hero=True)
    plain = money.rank_frames([(0.0, sharp)], hero=False)
    assert flagged["score"] > plain["score"]


def test_hold_catches_up_nearby_and_cold_opens_a_later_hero():
    ranges = [{"start": 0.0, "end": 4.0}]
    near = {"t": 0.6, "score": 4.0, "range_index": 0, "luma": 80, "sharp": 40}
    plan = money.hold_plan(near, ranges, 25)
    assert plan["reason"] == "catch-up" and abs(plan["hold_sec"] - 0.6) < 1e-6 and plan["hold_frames"] >= 1
    later = {"t": 3.2, "score": 5.0, "range_index": 0, "luma": 80, "sharp": 40}
    cold = money.hold_plan(later, ranges, 25)
    assert cold["reason"] == "cold-open" and abs(cold["hold_sec"] - money.COLD_OPEN_SEC) < 1e-6
    other = {"t": 8.0, "score": 6.0, "range_index": 1, "luma": 90, "sharp": 50}
    opened = money.hold_plan(other, ranges + [{"start": 6.0, "end": 10.0}], 25)
    assert opened["reason"] == "cold-open"
    already = money.hold_plan({"t": 0.0, "score": 1, "range_index": 0, "luma": 1, "sharp": 1}, ranges, 25)
    assert already["reason"] == "already-open" and already["hold_frames"] == 0


def test_money_shot_switch_is_on_unless_turned_off(monkeypatch, tmp_path):
    assert money.enabled(None, {}) is True
    for off in ("0", "false", "off", "no"):
        assert money.enabled(None, {"SHORTRIZZ_MONEY_SHOT": off}) is False
    assert money.enabled(None, {"SHORTRIZZ_MONEY_SHOT": "1"}) is True
    # CLI wins over the environment, the environment wins over the config file.
    assert money.enabled(True, {"SHORTRIZZ_MONEY_SHOT": "0"}) is True
    assert money.enabled(False, {"SHORTRIZZ_MONEY_SHOT": "1"}) is False
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"money_shot": False}))
    monkeypatch.setenv("SHORTRIZZ_CONFIG", str(cfg))
    monkeypatch.delenv("SHORTRIZZ_MONEY_SHOT", raising=False)
    assert money.enabled(None, {}) is False
    cfg.write_text(json.dumps({"money_shot": True}))
    assert money.enabled(None, {}) is True
    assert money.enabled(None, {"SHORTRIZZ_MONEY_SHOT": "0"}) is False


def test_opening_caption_and_hook_are_opaque_at_frame_zero():
    st = captions.load_style("hormozi")
    words = [{"w": "Hello", "s": 0.42, "e": 0.8}, {"w": "there", "s": 0.9, "e": 1.2}]
    plain = captions.build_ass(words, 0, 2, st, overlays=[{"text": "Big Hook", "t0": 0, "t1": 2.0, "pos": "top"}])
    hero = captions.build_ass(words, 0, 2, st, overlays=[{"text": "Big Hook", "t0": 0, "t1": 2.0, "pos": "top"}],
                              money_shot=True)
    first_plain = next(l for l in plain.splitlines() if l.startswith("Dialogue: 0,"))
    first_hero = next(l for l in hero.splitlines() if l.startswith("Dialogue: 0,"))
    assert first_plain.startswith("Dialogue: 0,0:00:00.42,")
    assert "\\fad(40,0)" in first_plain
    assert first_hero.startswith("Dialogue: 0,0:00:00.00,")
    assert "\\fad(40,0)" not in first_hero
    assert "\\fad(120,180)" in plain and "\\fad(0,180)" in hero
    assert "\\fad(120,180)" not in hero
    # the second word is not pulled back to zero
    assert "0:00:00.90" in hero


def test_picks_keep_an_explicit_hero():
    from shortrizz import picks
    words = [{"w": "hello", "s": 0.2, "e": 0.5}, {"w": "there", "s": 0.6, "e": 0.9}]
    doc = {"version": 1, "kind": "shorts", "source": {"video_path": "/tmp/x.mp4"}, "settings": {},
           "picks": [{"id": "p01", "order": 1, "enabled": True, "start": 0.1, "end": 1.0, "score": 80,
                      "title": "T", "word_range": [0, 1], "hero": True, "hero_time": 0.7}]}
    clip = picks.clips_for_render(doc, words)[0]
    assert clip["hero"] is True and clip["hero_time"] == 0.7
    plain = picks.clips_for_render(
        {**doc, "picks": [{**doc["picks"][0], "hero": False, "hero_time": None}]}, words)[0]
    assert "hero" not in plain and "hero_time" not in plain


def test_cli_no_money_shot_reaches_the_pipeline(monkeypatch):
    seen = {}
    monkeypatch.setattr(pipeline, "highlights", lambda *a, **kw: seen.update(kw) or {"format": "highlights", "clips": []})
    monkeypatch.setattr(pipeline, "summary", lambda doc: {})
    cli.main(["highlights", "vid.mp4", "--no-captions", "--json", "--no-money-shot"])
    assert seen["money_shot"] is False
    seen.clear()
    cli.main(["highlights", "vid.mp4", "--no-captions", "--json"])
    assert seen["money_shot"] is None


def _run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def _fixture(tmp_path):
    """Black, then a sharp green grid, then the same grid blurred. A sine keeps the audio check honest."""
    src = tmp_path / "src.mp4"
    _run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=320x180:r=25:d=0.56",
        "-f", "lavfi", "-i", "color=c=0x00d200:s=320x180:r=25:d=0.80,drawgrid=width=12:height=12:thickness=2:color=black",
        "-f", "lavfi", "-i", "color=c=0x00d200:s=320x180:r=25:d=0.64,drawgrid=width=12:height=12:thickness=2:color=black,gblur=sigma=16",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map", "[v]", "-map", "3:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-preset", "ultrafast",
        "-c:a", "aac", "-shortest", str(src),
    ])
    return src


def _frame(path, t=0.0):
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
        "-of", "json", str(path)]))
    w = int(probe["streams"][0]["width"])
    h = int(probe["streams"][0]["height"])
    raw = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path), "-ss", f"{t:.3f}", "-frames:v", "1",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stderr=subprocess.DEVNULL)
    arr = np.frombuffer(raw, np.uint8).reshape(h, w, 3)
    return arr


def _stats(rgb):
    import cv2
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    return {
        "luma": float(gray.mean()),
        "std": float(gray.std()),
        "sharp": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        "white": int(np.sum(np.min(rgb, axis=2) > 185)),
    }


def _duration(path):
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)]))
    return float(probe["format"]["duration"])


def _rms(path, seconds=0.2):
    raw = subprocess.check_output([
        "ffmpeg", "-v", "error", "-t", f"{seconds}", "-i", str(path),
        "-ac", "1", "-f", "s16le", "-"], stderr=subprocess.DEVNULL)
    samples = np.frombuffer(raw, np.int16).astype(np.float32)
    assert samples.size > 100
    return float(np.sqrt(np.mean(samples ** 2)))


def _words():
    return [{"w": "hello", "s": 0.08, "e": 0.30}, {"w": "there", "s": 0.78, "e": 1.10}]


def _assert_hero(rgb, where):
    # Judge the subject, not the letterbox bars on a vertical short.
    h, w, _ = rgb.shape
    if h > w:
        y0, y1 = int(h * 0.40), int(h * 0.60)
        x0, x1 = int(w * 0.25), int(w * 0.75)
        subject = _stats(rgb[y0:y1, x0:x1])
    else:
        subject = _stats(rgb)
    assert subject["luma"] > 35, f"{where} frame 0 is near-black ({subject})"
    assert subject["std"] > 12, f"{where} frame 0 is blank ({subject})"
    assert subject["sharp"] > 20, f"{where} frame 0 is blurry ({subject})"
    assert _stats(rgb)["white"] > 40, f"{where} frame 0 has no readable caption ({_stats(rgb)})"


@pytestmark_ffmpeg
def test_frame_zero_is_the_hero_for_shorts_and_highlights(tmp_path):
    src = _fixture(tmp_path)
    words = _words()
    opts = {"enabled": True, "edit": "off", "llm_edits": False, "hook_text": True, "loudnorm": False,
            "cold_open": False, "tts_intro": False, "tts_outro": False, "zoom": False, "loop": False,
            "emoji": False, "sfx": False}
    clip = {"start": 0.0, "end": 2.0, "title": "Big Hook", "virality_score": 90, "word_range": [0, 1],
            "why_it_hooks": "the grid", "caption": "watch", "hashtags": ["#shorts"], "word_start": 0.78}

    def render_short(folder, flag):
        out = tmp_path / folder
        out.mkdir()
        pipeline.render_clips(src, words, [clip], out, "hormozi", reframe_mode="letterbox", preset="ultrafast",
                              edit_opts=opts, money_shot=flag)
        return out / "01-big-hook.mp4"

    short_on = render_short("on", True)
    short_off = render_short("off", False)
    on = _frame(short_on)
    off = _frame(short_off)
    _assert_hero(on, "short")
    assert _stats(off)["luma"] < 18
    assert abs(_duration(short_on) - _duration(short_off)) < 0.12
    assert _rms(short_on) > 200 and abs(_rms(short_on) - _rms(short_off)) / _rms(short_off) < 0.35

    from shortrizz import highlights as HL
    seg = {"start": 0.0, "end": 2.0, "title": "Big Hook", "virality_score": 90, "word_range": [0, 1], "word_start": 0.78}
    kw = dict(words=words, style=captions.load_style("hormozi"), crossfade=0, loudnorm=False,
              preset="ultrafast", crf=16)
    hl_on = tmp_path / "hl-on.mp4"
    hl_off = tmp_path / "hl-off.mp4"
    info_on = HL.render_highlights(src, [seg], hl_on, money_shot=True, **kw)
    info_off = HL.render_highlights(src, [seg], hl_off, money_shot=False, **kw)
    _assert_hero(_frame(hl_on), "highlights")
    assert _stats(_frame(hl_off))["luma"] < 18
    assert info_on["money_shot"]["enabled"] is True and info_on["money_shot"]["reason"] in ("catch-up", "cold-open")
    assert info_off["money_shot"]["enabled"] is False
    assert abs(info_on["duration"] - info_off["duration"]) < 0.12
    assert abs(_duration(hl_on) - _duration(hl_off)) < 0.12
    assert _rms(hl_on) > 200 and abs(_rms(hl_on) - _rms(hl_off)) / _rms(hl_off) < 0.35
    # past the hold, the file is back on the real clock (not a frozen poster for the whole reel)
    later = _stats(_frame(hl_on, 1.55))
    assert later["sharp"] < _stats(_frame(hl_on))["sharp"] * 0.5
