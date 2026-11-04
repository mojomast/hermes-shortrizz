import json
import numpy as np
import pytest
from shortrizz import captions, clipselect, reframe, publish

def mk_words(text, start=0.0, step=0.3):
    out, t = [], start
    for w in text.split():
        out.append({"w": w, "s": round(t, 2), "e": round(t + step * 0.8, 2)}); t += step
    return out

SENT = "This is the hook you never saw coming. " + "We talk about money and the biggest mistake. " * 80
WORDS = mk_words(SENT)
TR = {"segments": [{"s": WORDS[0]["s"], "e": WORDS[-1]["e"], "text": SENT, "words": WORDS}]}

def test_pages_respect_max_words():
    for st in captions.STYLES.values():
        for pg in captions.pages(WORDS, st["max_words"]):
            assert 1 <= len(pg) <= st["max_words"] + 1

def test_ass_has_one_event_per_word_and_highlight():
    st = captions.load_style("hormozi")
    ass = captions.build_ass(WORDS, 0, 10, st)
    events = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    n = sum(1 for w in WORDS if w["e"] > 0 and w["s"] < 10)
    assert len(events) == n
    assert captions.ass_color(st["highlight"]) in events[0]
    assert "PlayResY: 1920" in ass
    assert "HOOK" in ass  # uppercase style

def test_ass_color_is_bgr():
    assert captions.ass_color("#FFE500") == "&H0000E5FF"

def test_custom_style_overrides_base(tmp_path):
    p = tmp_path / "s.json"; p.write_text(json.dumps({"base": "abdaal", "size": 77}))
    st = captions.load_style(str(p))
    assert st["size"] == 77 and st["font"] == captions.STYLES["abdaal"]["font"]
    with pytest.raises(SystemExit):
        captions.load_style("nope")

def test_snap_lands_on_sentence_boundaries_within_limits():
    a, b = clipselect.snap(WORDS, 3.0, 40.0, 30, 60)
    assert a == 0 or WORDS[a - 1]["w"].endswith(".")
    assert WORDS[b]["w"].endswith(".")
    assert 30 <= WORDS[b]["e"] - WORDS[a]["s"] <= 60

def test_select_heuristic_non_overlapping():
    clips = clipselect.select(TR, 3, 20, 40, use_llm=False)
    assert len(clips) == 3
    spans = sorted((c["start"], c["end"]) for c in clips)
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        assert e1 <= s2
    assert all(0 <= c["virality_score"] <= 100 for c in clips)

def test_parse_json_handles_fences():
    assert clipselect.parse_json('```json\n{"clips": []}\n```') == {"clips": []}

def _track(cx, n, motion):
    return {"cx": cx, "last": n, "dets": {k: (cx - 20, 50, 40, 40, motion(k)) for k in range(n)}}

def test_plan_follows_speaker_without_jitter():
    n, fps = 120, 6  # 20 s
    left = _track(100, n, lambda k: 5.0 if k < 60 else 0.2)
    right = _track(380, n, lambda k: 0.2 if k < 60 else 5.0)
    xs = reframe.plan([left, right], n, fps, 4.0, 1920, 1080, 608)
    assert len(xs) == n
    assert xs[10] < 960 - 304 < xs[110]  # starts on left speaker, ends on right
    jumps = sum(1 for a, b in zip(xs, xs[1:]) if abs(a - b) > 50)
    assert jumps <= 2  # one hard cut, no flicker
    assert all(0 <= x <= 1920 - 608 for x in xs)

def test_plan_none_without_faces():
    assert reframe.plan([], 0, 6, 4.0, 1920, 1080, 608) is None

def test_letterbox_shapes():
    out = reframe.letterbox(np.zeros((1080, 1920, 3), np.uint8), 1080, 1920)
    assert out.shape == (1920, 1080, 3)
    out = reframe.letterbox(np.zeros((1920, 1080, 3), np.uint8), 1080, 1920)
    assert out.shape == (1920, 1080, 3)

def test_publish_dry_run(tmp_path):
    clips = [{"file": f"{i}.mp4", "title": f"T{i}", "caption": "c", "hashtags": ["#a"], "virality_score": i}
             for i in range(3)]
    (tmp_path / "clips.json").write_text(json.dumps({"clips": clips}))
    posts = publish.plan(tmp_path, ["youtube", "tiktok"], every_hours=12)
    assert len(posts) == 6 and all(p["status"] == "dry-run" for p in posts)
    assert posts[0]["file"] == "2.mp4" and posts[0]["title"].endswith("#Shorts")
    assert (tmp_path / "publish_plan.json").exists()
    with pytest.raises(NotImplementedError):
        publish.PUBLISHERS["tiktok"].upload({})

def test_snap_without_punctuation_keeps_distinct_clips():
    # Whisper sometimes returns almost no punctuation: picks must not collapse onto one sentence
    words = []
    t = 0.0
    for k in range(600):
        words.append({"w": f"word{k}", "s": round(t, 2), "e": round(t + 0.25, 2), "ss": k % 20 == 0, "se": k % 20 == 19})
        t += 0.3 + (0.5 if k % 20 == 19 else 0)
    a = clipselect.snap(words, 20, 55, 30, 60)
    b = clipselect.snap(words, 100, 140, 30, 60)
    assert a and b and a != b
    assert abs(words[a[0]]["s"] - 20) <= 4 and abs(words[b[0]]["s"] - 100) <= 4
    assert words[a[0]]["ss"] and words[b[1]]["se"]
