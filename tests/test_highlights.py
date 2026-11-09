"""Long-form 16:9 highlights: selection, layout, and a real ffmpeg render."""
import json, shutil, subprocess

import pytest

from shortrizz import captions, clipselect, highlights as HL
from shortrizz import cli, hermes_tools as H
from shortrizz.transcribe import all_words

pytestmark_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs ffmpeg")


def _words(text, start=0.0, step=0.32):
    out, t = [], start
    for w in text.split():
        out.append({"w": w, "s": round(t, 2), "e": round(t + step * 0.8, 2)})
        t += step
    return out


def _tr(paragraphs):
    words = []
    for text, start in paragraphs:
        words.extend(_words(text, start))
    return {"segments": [{"s": words[0]["s"], "e": words[-1]["e"],
                          "text": " ".join(w["w"] for w in words), "words": words}]}


P1 = "The secret you never knew about saving money starts with one daily habit that most busy people skip without noticing the cost."
P2 = "Everyone makes this huge mistake on their first real job and then wonders why nothing about the outcome ever changes afterwards at all."
P3 = "Here is the truth about how people actually learn a new skill when nobody is watching them practice the boring part."


def _clip(start, dur, score, text):
    return {"start": start, "end": start + dur, "word_start": start + 0.1, "word_end": start + dur - 0.1,
            "virality_score": score, "transcript": text, "title": text.split()[0]}


def test_choose_respects_budget_score_and_order():
    clips = [
        _clip(0, 20, 90, "alpha unique story about rockets and orbits over the pacific"),
        _clip(30, 20, 80, "bravo unique story about oceans and tides along the coast"),
        _clip(60, 20, 70, "charlie unique story about forests and rain in the north"),
    ]
    got = clipselect.choose_highlights(clips, target=45, max_clips=5, min_score=0, overshoot=0.1)
    assert [c["start"] for c in got] == [0, 30]
    assert abs(sum(c["end"] - c["start"] for c in got) - 40) < 0.01
    scored = clipselect.choose_highlights(clips, target=45, max_clips=5, min_score=0, order="score", overshoot=0.1)
    assert [c["virality_score"] for c in scored] == [90, 80]
    high = clipselect.choose_highlights(clips, target=0, max_clips=5, min_score=75)
    assert [c["virality_score"] for c in high] == [90, 80]


def test_choose_drops_overlaps_and_near_duplicate_text():
    same = "the secret you never knew about saving money starts here today"
    other = "everyone makes this huge mistake on their first real job today"
    a, b, c = _clip(0, 20, 90, same), _clip(40, 20, 88, same), _clip(80, 20, 70, other)
    assert clipselect.near_duplicate(a, b)
    assert not clipselect.near_duplicate(a, c)
    got = clipselect.choose_highlights([a, b, c], target=0, max_clips=5, min_score=0)
    assert len(got) == 2
    overlap = _clip(10, 30, 80, "two totally different words about something else entirely here")
    assert clipselect.near_duplicate(a, overlap)
    assert len(clipselect.choose_highlights([a, overlap], target=0, max_clips=5, min_score=0)) == 1


def test_choose_keeps_one_segment_when_nothing_fits():
    long = _clip(0, 100, 90, "one long unique segment about glaciers and ice sheets drifting")
    short = _clip(200, 15, 60, "a short unique note about sparrows nesting in the garden")
    only = clipselect.choose_highlights([long], target=20, max_clips=3, min_score=0, overshoot=0.1)
    assert len(only) == 1 and only[0]["start"] == 0
    fitted = clipselect.choose_highlights([long, short], target=30, max_clips=3, min_score=0, overshoot=0.1)
    assert len(fitted) == 1 and fitted[0]["start"] == 200


def test_select_highlights_sentence_bounds_chrono_and_dedupe():
    tr = _tr([(P1, 0.4), (P2, 12.0), (P1, 24.0)])
    clips = clipselect.select_highlights(tr, 5, 10, target=60, max_clips=5, min_score=0, use_llm=False)
    assert len(clips) == 2  # the repeated paragraph is a near-duplicate
    words = all_words(tr)
    starts = []
    for c in clips:
        a, b = c["word_range"]
        assert words[b]["w"].endswith(".")
        assert c["start"] <= words[a]["s"] + 1e-6
        assert c["end"] + 1e-6 >= words[b]["e"]
        if a > 0:
            assert c["start"] >= words[a - 1]["e"] - 1e-6
        if b + 1 < len(words):
            assert c["end"] <= words[b + 1]["s"] + 1e-6
        starts.append(c["start"])
    assert starts == sorted(starts)
    texts = [c["transcript"] for c in clips]
    assert not clipselect.near_duplicate({"start": 0, "end": 1, "word_start": 0, "word_end": 1, "transcript": texts[0]},
                                         {"start": 50, "end": 51, "word_start": 50, "word_end": 51, "transcript": texts[1]})
    by_score = clipselect.select_highlights(tr, 5, 10, target=60, max_clips=5, min_score=0,
                                            order="score", use_llm=False)
    scores = [c["virality_score"] for c in by_score]
    assert scores == sorted(scores, reverse=True)


def test_separate_ranges_trims_padding_not_words():
    segs = clipselect.separate_ranges([
        {"start": 0.0, "end": 10.3, "word_start": 0.2, "word_end": 10.0},
        {"start": 10.1, "end": 20.0, "word_start": 10.4, "word_end": 19.5},
    ])
    assert segs[0]["end"] <= segs[1]["start"] + 1e-6
    assert segs[0]["end"] >= 10.0 - 1e-6
    assert segs[1]["start"] <= 10.4 + 1e-6


def test_fit_16x9_keeps_frame_and_pads():
    assert HL.fit_16x9(1920, 1080) == (1920, 1080)
    assert HL.fit_16x9(1280, 720) == (1280, 720)
    assert HL.fit_16x9(3840, 2160, 1920) == (1920, 1080)
    w, h = HL.fit_16x9(200, 200)
    assert abs(w / h - 16 / 9) < 0.02 and w % 2 == 0 and h % 2 == 0
    assert w <= 200 or h <= 200  # the picture itself is not upscaled; one side matches the source
    vw, vh = HL.fit_16x9(1080, 1920, 1920)
    assert (vw, vh) == (1920, 1080)
    assert HL.norm_fps(60) == 30 and HL.norm_fps(25) == 25
    assert HL.transition_offsets([10, 10, 10], 0.5) == [9.5, 19.0]
    assert HL.joined_duration([10, 10, 10], 0.5) == 29.0


def test_landscape_captions_sit_in_the_lower_third():
    st = captions.for_landscape(captions.load_style("hormozi"), 1920, 1080)
    words = _words("Hello there friend today.", 0.2)
    ass = captions.build_ass(words, 0, 5, st, W=1920, H=1080)
    assert "PlayResX: 1920" in ass and "PlayResY: 1080" in ass
    import re
    ys = [int(y) for y in re.findall(r"\\pos\(\d+,(\d+)\)", ass)]
    assert ys and all(1080 * 2 / 3 < y < 1080 * 0.92 for y in ys)
    kept = HL.caption_window(
        [{"w": "a", "s": 0.05, "e": 0.2}, {"w": "b", "s": 0.5, "e": 0.8}, {"w": "c", "s": 1.7, "e": 1.95}],
        2.0, 0.4, True, True)
    assert [w["w"] for w in kept] == ["b"]


def test_chapters_list_score_and_source_time():
    txt = HL.chapters_text([
        {"out_start": 0, "title": "First hook", "virality_score": 82, "start": 62, "end": 90},
        {"out_start": 27.4, "title": "Second bit", "virality_score": 74, "start": 250, "end": 280},
    ])
    lines = txt.strip().splitlines()
    assert lines[0].startswith("0:00 ") and "score 82" in lines[0] and "source 1:02–1:30" in lines[0]
    assert lines[1].startswith("0:27 ") and "score 74" in lines[1]


def test_highlights_cli_and_plugin(monkeypatch, tmp_path):
    from shortrizz import pipeline
    seen = {}

    def fake(*_a, **kw):
        seen.update(kw)
        return {"format": "highlights", "clips": [], "source": {}, "file": "highlights.mp4",
                "out_dir": str(tmp_path), "duration": 1, "width": 1920, "height": 1080, "order": kw["order"],
                "chapters": "chapters.txt", "manifest": "highlights.json"}

    monkeypatch.setattr(pipeline, "highlights", fake)
    cli.main(["highlights", "vid.mp4", "--duration", "90", "--max-clips", "4", "--order", "score",
              "--hard-cuts", "--no-captions", "--min-score", "70", "--json"])
    assert seen["target"] == 90 and seen["max_clips"] == 4 and seen["order"] == "score"
    assert seen["crossfade"] == 0 and seen["captions"] is False and seen["min_score"] == 70

    monkeypatch.setenv("SHORTRIZZ_RUNS", str(tmp_path))
    monkeypatch.setattr(H, "_start_job", lambda kind, argv, out: seen.update(kind=kind, argv=argv) or
                        {"job_id": "highlights-x", "kind": kind, "pid": 1, "out_dir": str(out),
                         "log": str(out / "l"), "started": 0})
    monkeypatch.setattr(H, "_wait", lambda job, w: {"status": "running", "job_id": job["job_id"]})
    out = json.loads(H.make_highlights_tool({"source": "https://example.com/v", "duration": 90, "order": "score",
                                             "hard_cuts": True, "captions": False, "out_dir": str(tmp_path)}))
    assert out["status"] == "running"
    assert seen["kind"] == "highlights" and seen["argv"][0] == "highlights"
    assert "--hard-cuts" in seen["argv"] and "--no-captions" in seen["argv"] and "score" in seen["argv"]
    assert json.loads(H.make_highlights_tool({}))["ok"] is False
    assert json.loads(H.make_highlights_tool({"source": "https://example.com/v", "order": "random"}))["ok"] is False
    assert json.loads(H.make_highlights_tool({"source": "https://example.com/v", "min_score": 140}))["ok"] is False
    names = [s["name"] for s in H.tool_specs()]
    assert "shortrizz_make_highlights" in names


def test_restyle_and_publish_reject_highlights(tmp_path):
    from shortrizz import pipeline, publish
    run = tmp_path / "hl"
    run.mkdir()
    (run / "clips.json").write_text(json.dumps({"format": "highlights", "clips": [
        {"file": "highlights.mp4", "title": "A", "caption": "c", "hashtags": [], "virality_score": 80}]}))
    with pytest.raises(SystemExit):
        pipeline.restyle(run, "hormozi")
    with pytest.raises(SystemExit):
        publish.plan(run, ["youtube"])
    assert json.loads(H.publish_plan_tool({"run_dir": str(run)}))["ok"] is False


def _run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def _pixel(path, t, x, y, w, h):
    raw = subprocess.check_output([
        "ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stderr=subprocess.DEVNULL)
    assert len(raw) >= w * h * 3
    i = (min(h - 1, y) * w + min(w - 1, x)) * 3
    return raw[i], raw[i + 1], raw[i + 2]


def _probe(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,codec_name",
        "-show_entries", "format=duration", "-of", "json", str(path)])
    return json.loads(out)


@pytestmark_ffmpeg
def test_square_source_is_padded_not_cropped(tmp_path):
    src = tmp_path / "sq.mp4"
    _run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=200x200:r=25:d=2",
          "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
          "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)])
    out = tmp_path / "out.mp4"
    info = HL.render_highlights(src, [{"start": 0.1, "end": 1.5, "title": "red"}], out,
                                crossfade=0, loudnorm=False, preset="ultrafast", crf=30, style=None, watermark=None)
    assert abs(info["width"] / info["height"] - 16 / 9) < 0.02
    probe = _probe(out)
    streams = probe["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    assert video["width"] == info["width"] and video["height"] == info["height"]
    assert any(s["codec_type"] == "audio" for s in streams)
    assert HL.render.check_audio(out) > -45
    corner = _pixel(out, 0.3, 2, 2, info["width"], info["height"])
    cx, cy = info["width"] // 2, info["height"] // 2
    center = _pixel(out, 0.3, cx, cy, info["width"], info["height"])
    assert sum(corner) < 50  # pad, not a stretched or cropped fill
    assert center[0] > 150 and center[1] < 90 and center[2] < 90


@pytestmark_ffmpeg
def test_hard_cuts_keep_length_and_crossfade_shortens(tmp_path):
    src = tmp_path / "tone.mp4"
    _run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=25:d=8",
          "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8",
          "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)])
    segs = [{"start": 0.2, "end": 2.4, "title": "a"}, {"start": 3.0, "end": 5.2, "title": "b"}]
    kw = dict(loudnorm=False, preset="ultrafast", crf=32, style=None)
    hard = HL.render_highlights(src, segs, tmp_path / "hard.mp4", crossfade=0, **kw)
    fade = HL.render_highlights(src, segs, tmp_path / "fade.mp4", crossfade=0.35, **kw)
    span = (2.4 - 0.2) + (5.2 - 3.0)
    assert abs(hard["duration"] - span) < 0.6
    assert fade["crossfade"] > 0.2
    assert fade["duration"] < hard["duration"] - 0.15
    assert abs(fade["duration"] - (span - fade["crossfade"])) < 0.6
    assert abs(hard["width"] / hard["height"] - 16 / 9) < 0.02


@pytestmark_ffmpeg
def test_pipeline_highlights_on_sample_is_16x9_with_two_segments(monkeypatch, tmp_path):
    """End to end through pipeline.highlights: real file in, real 16:9 file out. Transcript is injected."""
    from shortrizz import pipeline
    src = tmp_path / "bars.mp4"
    half = 9
    _run(["ffmpeg", "-v", "error", "-y",
          "-f", "lavfi", "-i", f"color=c=red:s=640x360:r=25:d={half}",
          "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={half}",
          "-f", "lavfi", "-i", f"color=c=blue:s=640x360:r=25:d={half}",
          "-f", "lavfi", "-i", f"sine=frequency=660:sample_rate=48000:duration={half}",
          "-filter_complex", "[0:v][1:a][2:v][3:a]concat=n=2:v=1:a=1[v][a]",
          "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)])
    tr = _tr([(P1, 0.4), (P3, 9.4)])
    monkeypatch.setattr(pipeline.T, "transcribe", lambda *a, **k: tr)
    out = tmp_path / "run"
    doc = pipeline.highlights(src, out, target=40, max_clips=4, min_score=0, min_len=5, max_len=10,
                              order="chrono", crossfade=0.3, captions=True, style="hormozi",
                              cache=tmp_path / "cache", use_llm=False, preset="ultrafast", crf=30,
                              watermark=False, loudnorm=True)
    assert doc["format"] == "highlights" and len(doc["clips"]) >= 2
    assert doc["clips"][0]["end"] <= half + 0.3
    assert doc["clips"][-1]["start"] >= half - 0.4
    mp4 = out / "highlights.mp4"
    assert mp4.is_file() and (out / "chapters.txt").is_file() and (out / "highlights.json").is_file()
    assert (out / "clips.json").is_file() and (out / "index.html").is_file()
    chapters = (out / "chapters.txt").read_text()
    assert chapters.startswith("0:00 ") and "score" in chapters and "source" in chapters
    man = json.loads((out / "highlights.json").read_text())
    assert len(man["segments"]) == len(doc["clips"])
    assert abs(man["width"] / man["height"] - 16 / 9) < 0.02
    picks_doc = json.loads((out / "picks.json").read_text())
    assert picks_doc["version"] == 1 and picks_doc["kind"] == "highlights"
    assert (out / "picks.v1.json").is_file()
    assert picks_doc["picks"] and len(picks_doc["picks"]) == len(doc["clips"])
    for pick in picks_doc["picks"]:
        still = out / pick["still"]
        assert still.is_file() and still.stat().st_size > 100
    probe = _probe(mp4)
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    assert video["codec_name"] == "h264" and audio["codec_name"] == "aac"
    assert video["width"] == 640 and video["height"] == 360
    dur = float(probe["format"]["duration"])
    n = len(doc["clips"])
    src_sum = sum(c["end"] - c["start"] for c in doc["clips"])
    expect = src_sum - doc["crossfade_sec"] * (n - 1)
    assert abs(dur - expect) < 1.0
    assert abs(dur - doc["duration"]) < 0.15
    assert HL.render.check_audio(mp4) > -45
    w, h = video["width"], video["height"]
    early = _pixel(mp4, 0.45, w // 2, h // 2, w, h)
    late = _pixel(mp4, max(0.2, dur - 0.45), w // 2, h // 2, w, h)
    assert early[0] > 140 and early[0] > early[2] + 40  # first segment is the red half
    assert late[2] > 140 and late[2] > late[0] + 40      # last segment is the blue half
    page = (out / "index.html").read_text()
    assert "highlights.mp4" in page and "16:9" in page
