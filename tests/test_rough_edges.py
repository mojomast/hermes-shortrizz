import json, argparse
import numpy as np
from shortrizz import edits as E, captions, cli, watermark as WM, clipselect

def W(spec):
    return [{"w": w, "s": s, "e": e} for w, s, e in spec]

def seq(text, t0=0.0, step=0.3, gap=0.05):
    out, t = [], t0
    for w in text.split():
        out.append({"w": w, "s": round(t, 3), "e": round(t + step - gap, 3)}); t += step
    return out

# 3. meaning words are protected; true fillers, repeats and restarts still go
def test_drop_rules_protect_meaning_words():
    ws = seq("so I think which is the point, you know, that's that's huge and we went, we drove home like, fast")
    b = len(ws) - 1
    idx = lambda w, k=0: [i for i, x in enumerate(ws) if E.norm(x["w"]) == w][k]
    assert not E.drop_allowed(ws, idx("i"), idx("i"), b)                       # lone "I"
    assert not E.drop_allowed(ws, idx("which"), idx("is"), b)                  # "which is"
    assert E.drop_allowed(ws, idx("you"), idx("know"), b)                      # filler phrase
    assert E.drop_allowed(ws, idx("that's"), idx("that's"), b)                 # stutter repeat
    assert E.drop_allowed(ws, idx("we"), idx("went"), b, "false_start")        # restart
    assert E.drop_allowed(ws, idx("like"), idx("like"), b)                     # "like," as filler
    assert not E.drop_allowed(seq("I like it a lot"), 1, 1, 4)                # "I like it" is a verb
    plan = {"drop": [{"w0": idx("i"), "w1": idx("i")}, {"w0": idx("which"), "w1": idx("is")},
                     {"w0": idx("you"), "w1": idx("know")}]}
    v = E.validate(plan, ws, 0, b)
    assert [d["text"] for d in v["drop"]] == ["you know,"]

# 1. cold open fires: long LLM picks are trimmed to a clause, missing ones get a heuristic pick
def test_cold_open_trims_and_falls_back():
    ws = seq("Hello and welcome to the show today. We talk about money. Nobody tells you the biggest mistake "
             "is waiting, and that is why most people never start. Then we move on to other things here.")
    b = len(ws) - 1
    long_pick = {"w0": 10, "w1": 30}
    co = E.pick_cold_open(ws, 0, b, long_pick)
    assert co and 1.5 <= ws[co["w1"]]["e"] - ws[co["w0"]]["s"] <= 5.0
    co2 = E.pick_cold_open(ws, 0, b, {})
    assert co2 and co2["by"] == "heuristic" and ws[co2["w0"]]["s"] >= 3.0
    assert 2.0 <= ws[co2["w1"]]["e"] - ws[co2["w0"]]["s"] <= 5.0

# 2. extras are paid for by trimming the body: final <= original and <= max length
def test_length_budget_trims_body(monkeypatch, tmp_path):
    ws = seq(" ".join(f"word{i}." if i % 8 == 7 else f"word{i}" for i in range(140)), step=0.3)  # ~42 s
    clip = {"start": 0.0, "end": ws[-1]["e"], "word_range": [0, len(ws) - 1], "title": "t"}
    from shortrizz import tts
    monkeypatch.setattr(tts, "speak", lambda text, wav, speed=1.1: 3.0)
    monkeypatch.setattr(E, "speech_mask", lambda *a, **k: None)
    opts = {**E.DEFAULTS, **{k: True for k in E.OPT_INS}, "loop": False, "max_len": 40, "min_len": 30}
    edl = {"gap_policy": E.PRESETS["normal"], "drop": [], "intro": "a narrator line", "cta": "Follow",
           "cold_open": {"w0": 16, "w1": 23, "text": "x"}, "hook_text": "Hook", "emphasis": [], "emoji": []}
    items, cw, ovl, fx, rep, total = E.assemble("x.mp4", ws, clip, edl, opts, captions.load_style("hormozi"),
                                                tmp_path, "c")
    assert total <= 40.05 and total <= clip["end"] - clip["start"]
    assert rep["trimmed_tail_sec"] > 0 and rep["intro_tts"] and rep["cold_open"]
    assert ws[[w for w in cw if "i" in w][-1]["i"]]["w"].endswith(".")        # body ends on a sentence

# 4. captions move off a face that sits on the caption line
def test_caption_avoids_face():
    st = captions.load_style("hormozi")
    base = min(int(1920 * st["pos_y"]), int(captions.SAFE_BOTTOM_Y - st["size"] * 0.6))
    face = (300, base - 250, 780, base + 120)                      # mouth right on the caption line
    track = [(t * 0.5, [face]) for t in range(20)]
    y = E.caption_y_fn(track, base, st["size"])(1.0, 2.0)
    half = st["size"] * 0.62
    assert y + half <= face[1] or y - half >= face[3]               # clear of the face box
    assert E.SAFE["top"] < y - half and y + half <= E.SAFE["bottom_y"] + 1
    assert E.caption_y_fn([(0, [])], base, st["size"])(0, 1) == base  # no face -> default spot

# 5. emoji never land on a face
def test_emoji_avoids_face():
    face = (120, 500, 520, 1000)                                    # covers the default upper-left spot
    track = [(t * 0.5, [face]) for t in range(10)]
    places = E.emoji_places(track, [(1.0, "🔥")], lambda a, b: 1160)
    x, y, sz = places[1.0]
    assert not (face[0] < x < face[2] and face[1] < y < face[3])
    full = [(t * 0.5, [(0, 0, 1080, 1920)]) for t in range(10)]
    assert E.emoji_places(full, [(1.0, "🔥")], lambda a, b: 1160)[1.0] is None   # skip, never cover

# 7. watermark gets a backing on white and stays clean on dark
def test_watermark_readable_on_white():
    st = dict(WM.DEFAULTS, watermark=True, opacity=0.72)
    ov = WM.Overlay(1080, 1920, st)
    white = ov.apply(np.full((1920, 1080, 3), 250, np.uint8))
    y0, y1, x0, x1 = ov.pbox
    region = white[y0:y1, x0:x1].astype(float)
    assert region.min() < 160                                       # dark outline/pill gives contrast
    dark = ov.apply(np.full((1920, 1080, 3), 20, np.uint8))
    assert dark[y0:y1, x0:x1].max() > 150                           # white mark visible on dark

# 8. --length tight: 20-35 s band reaches selection and the edit budget
def test_length_tight_preset(monkeypatch):
    seen = {}
    from shortrizz import pipeline
    monkeypatch.setattr(pipeline, "make", lambda *a, **k: seen.update(mn=a[3], mx=a[4]) or {"clips": []})
    cli.main(["video.mp4", "--length", "tight"])
    assert (seen["mn"], seen["mx"]) == (20.0, 35.0)
    words = seq(" ".join(f"w{i}." if i % 6 == 5 else f"w{i}" for i in range(600)), step=0.3)
    tr = {"segments": [{"s": words[0]["s"], "e": words[-1]["e"], "text": " ".join(w["w"] for w in words),
                        "words": words}]}
    clips = clipselect.select(tr, 3, 20, 35, use_llm=False)
    assert clips and all(20 - 0.5 <= c["end"] - c["start"] <= 35 + 0.5 for c in clips)

def test_caption_prefers_forehead_over_mouth_on_closeups():
    st = captions.load_style("hormozi")
    base = min(int(1920 * st["pos_y"]), int(captions.SAFE_BOTTOM_Y - st["size"] * 0.6))
    face = (248, 386, 842, 1178)                                    # real close-up from the S79 demo
    y = E.caption_y_fn([(0.0, [face]), (0.5, [face])], base, st["size"])(0.0, 0.5)
    assert y < face[1] + 0.25 * (face[3] - face[1])                 # above the eyes, not on the mouth

def _tight_setup(monkeypatch):
    ws = seq(" ".join(f"word{i}." if i % 8 == 7 else f"word{i}" for i in range(100)), step=0.3)  # ~30 s
    clip = {"start": 0.0, "end": ws[-1]["e"], "word_range": [0, len(ws) - 1], "title": "t"}
    from shortrizz import tts
    monkeypatch.setattr(tts, "speak", lambda text, wav, speed=1.1: 3.0)
    monkeypatch.setattr(E, "speech_mask", lambda *a, **k: None)
    edl = {"gap_policy": E.PRESETS["normal"], "drop": [], "intro": "a narrator line", "cta": "Follow",
           "cold_open": {"w0": 20, "w1": 33, "text": "x"}, "hook_text": "Hook", "emphasis": [], "emoji": []}
    return ws, clip, edl

# 1a. tight + all-fx keeps the speaker on screen >= 70%; explicit --tts-intro still forces it
def test_tight_allfx_skips_narrator_unless_explicit(monkeypatch, tmp_path):
    ws, clip, edl = _tight_setup(monkeypatch)
    st = captions.load_style("hormozi")
    opts = {**E.DEFAULTS, **{k: True for k in E.OPT_INS}, "loop": False, "max_len": 35, "min_len": 20}
    *_, rep, total = E.assemble("x.mp4", ws, clip, edl, opts, st, tmp_path, "c")
    assert not rep["intro_tts"] and not rep["outro_tts"] and "tts_intro" in rep["auto_skipped"]
    assert rep["kept_sec"] / total >= 0.70
    forced = {**opts, "explicit": ["tts_intro"]}
    *_, rep2, _ = E.assemble("x.mp4", ws, clip, edl, forced, st, tmp_path, "c")
    assert rep2["intro_tts"] and not rep2["outro_tts"]

# 1b. a second run on the same video avoids the first run's moments
def test_exclude_previous_ranges(tmp_path):
    from shortrizz import pipeline
    words = seq(" ".join(f"w{i}." if i % 6 == 5 else f"w{i}" for i in range(900)), step=0.3)
    tr = {"segments": [{"s": 0, "e": words[-1]["e"], "text": " ".join(w["w"] for w in words), "words": words}]}
    first = clipselect.select(tr, 3, 20, 35, use_llm=False)
    run = tmp_path / "run1"; run.mkdir()
    (run / "clips.json").write_text(json.dumps({"clips": first}))
    used = tmp_path / "v.used.json"
    pipeline._record_used(used, first, run)
    ex = pipeline.previous_ranges([run], used)
    second = clipselect.select(tr, 3, 20, 35, use_llm=False, exclude=ex)
    assert second and not any(a["start"] < b["end"] and a["end"] > b["start"] for a in second for b in first)
    p = argparse.ArgumentParser()
    seen = {}
    from shortrizz import pipeline as P
    import pytest
    mp = pytest.MonkeyPatch(); mp.setattr(P, "make", lambda *a, **k: seen.update(k) or {"clips": []})
    cli.main(["v.mp4", "--avoid-previous", "--exclude", str(run)]); mp.undo()
    assert seen["avoid_previous"] and seen["exclude"] == [str(run)]


# 1d. captions keep one zone per shot on a jittering close-up (no forehead/chest jumping)
def test_caption_zone_is_stable_on_closeups():
    st = captions.load_style("hormozi")
    base = min(int(1920 * st["pos_y"]), int(captions.SAFE_BOTTOM_Y - st["size"] * 0.6))
    import random
    random.seed(1)
    track = []
    for k in range(40):  # 20 s close-up whose box wobbles +-40 px
        d = random.randint(-40, 40)
        track.append((k * 0.5, [(250 + d, 420 + d, 830 + d, 1180 + d)]))
    fn = E.caption_y_fn(track, base, st["size"])
    ys = {fn(t, t + 0.8) for t in [x * 0.7 for x in range(25)]}
    assert len(ys) == 1

# 1c. hook text moves off a face that covers its usual top spot
def test_hook_avoids_face():
    face = (250, 330, 830, 1000)
    y, band = E.hook_y([(0.0, [face]), (1.0, [face]), (2.0, [face])], 2.8)
    default = max(int(1920 * 0.21), E.SAFE["top"] + 110)
    h = band[1] - band[0]
    hb = E.head_box(face)
    cost = lambda top: max(0.0, min(top + h, hb[3]) - max(top, hb[1]))
    assert cost(y) <= cost(default)                  # never worse than the usual spot
    assert E.SAFE["top"] <= y and y + h <= E.SAFE["bottom_y"] - 200
    low = (300, 900, 780, 1200)                      # head well below the usual spot: keep it
    assert E.hook_y([(0.0, [low])], 2.8)[0] == default

def test_hook_avoids_hair_and_side_of_face():
    face = (360, 640, 720, 1000)                     # eyes clear of the usual spot, but hair/forehead are under it
    hb = E.head_box(face)
    y, (y0, y1) = E.hook_y([(0.0, [face])], 2.8)
    default = max(int(1920 * 0.21), E.SAFE["top"] + 110)
    over = lambda a, b: max(0.0, min(b, hb[3]) - max(a, hb[1]))
    assert over(default, default + (y1 - y0)) > 0     # old rule would have kept it on the hair
    assert over(y0, y1) < over(default, default + (y1 - y0))
    assert E.SAFE["top"] <= y0 and y1 <= E.SAFE["bottom_y"] - 200
    assert hb[0] < face[0] and hb[2] > face[2] and hb[1] < face[1]   # sides and hair included

def test_hook_prefers_hair_over_mouth_when_it_must_overlap():
    face = (300, 560, 780, 1000)                     # big head filling the middle of the frame
    y, (y0, y1) = E.hook_y([(0.0, [face])], 2.8)
    eyes_to_chin = (face[1] + 0.25 * (face[3] - face[1]), face[3])
    assert max(0.0, min(y1, eyes_to_chin[1]) - max(y0, eyes_to_chin[0])) == 0

def test_end_card_hides_live_captions():
    from shortrizz import captions
    st = captions.load_style("hormozi")
    words = [{"w": f"w{i}", "s": i * 0.4, "e": i * 0.4 + 0.35} for i in range(30)]
    ovl = [{"text": "FOLLOW FOR MORE", "t0": 9.0, "t1": 12.0, "pos": "middle"}]
    ass = captions.build_ass(words, 0.0, 12.0, st, overlays=ovl)
    t = lambda x: sum(float(p) * m for p, m in zip(x.split(":"), (3600, 60, 1)))
    for line in ass.splitlines():
        if ",Cap," in line:
            s0, e0 = line.split(",")[1:3]
            assert t(e0) <= 9.0 + 1e-6 or t(s0) >= 12.0, line
    assert any(",Hook," in l and "FOLLOW" in l for l in ass.splitlines())
    y2, _ = E.hook_y([(0.0, [])], 2.8)
    assert y2 == max(int(1920 * 0.21), E.SAFE["top"] + 110)

# 1e. emoji fall back to smaller sizes / corners before skipping
def test_emoji_uses_fallback_spots():
    face = (200, 450, 880, 1100)                    # big centred face: the 150 px mid spots are blocked
    track = [(t * 0.5, [face]) for t in range(10)]
    pl = E.emoji_places(track, [(1.0, "🔥")], lambda a, b: 1160)[1.0]
    assert pl is not None
    x, y, sz = pl
    assert E.SAFE["top"] <= y - sz / 2 and y + sz / 2 <= E.SAFE["bottom_y"]
    assert not (face[0] < x < face[2] and face[1] < y < face[3])


def test_hook_band_matches_text_and_sits_above_eyes():
    from shortrizz import captions
    face = (400, 400, 700, 840)                      # real close-up from the power-grid QA clip
    y, (y0, y1) = E.hook_y([(t * 0.5, [face]) for t in range(6)], 2.8, text="Spinning Metal Stabilizes Grid")
    size, lines = captions.hook_size("SPINNING METAL STABILIZES GRID")
    assert abs((y1 - y0) - (lines * size * 1.05 + 30)) < 1
    ey = face[1] + 0.25 * (face[3] - face[1])
    assert max(0.0, min(y1, face[3]) - max(y0, ey)) <= 0.1 * (y1 - y0)   # above the eyes or under the chin


def _w(spec):
    out, t = [], 0.0
    for tok in spec.split():
        gap = 0.4 if tok.startswith("|") else 0.02
        tok = tok.lstrip("|"); t += gap
        out.append({"w": tok, "s": t, "e": t + 0.25}); t += 0.25
    return out

def test_natural_end_rejects_mid_phrase():
    ws = _w("we are going to inject code and this code can do anything. |it can copy")
    assert not E.natural_end(ws, 6)          # "and"
    assert not E.natural_end(ws, 10)         # "do"
    assert E.natural_end(ws, 11)             # "anything."

def test_cold_span_does_not_bleed_into_neighbours():
    ws = _w("a b c d e")
    s, e = E.cold_span(ws, 1, 3)
    assert s >= ws[0]["e"] and e <= ws[4]["s"]
    assert s <= ws[1]["s"] and e >= ws[3]["e"]

def test_tight_cold_open_never_shortened_mid_phrase():
    # cold open "we are going to inject code and this code can do anything." is ~3.3 s; tight clips shorten
    # it, but only to a phrase end, otherwise it is dropped instead of cut on "and"/"do"
    ws = _w("we are going to inject code and this code can do anything.")
    ends = [j for j in range(2, len(ws) - 1) if ws[j]["e"] - ws[0]["s"] <= 2.6 and E.natural_end(ws, j)]
    for j in ends:
        assert E.norm(ws[j]["w"]) not in E._DANGLING
    ws2 = _w("errors in what gets picked |may still degrade the model.")
    ok = [j for j in range(2, len(ws2) - 1) if E.natural_end(ws2, j)]
    assert ok == [4]                         # "picked" (pause after it), never "may"/"still"

def test_youtube_id_from_url_skips_network_lookup(tmp_path, monkeypatch):
    from shortrizz import download
    assert download._youtube_id("https://www.youtube.com/watch?v=Ln3AGdhSWd4") == "Ln3AGdhSWd4"
    assert download._youtube_id("https://youtu.be/Ln3AGdhSWd4?t=3") == "Ln3AGdhSWd4"
    (tmp_path / "Ln3AGdhSWd4.mp4").write_bytes(b"x")
    monkeypatch.setattr(download, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    p, meta = download.fetch("https://www.youtube.com/watch?v=Ln3AGdhSWd4", tmp_path)
    assert p.name == "Ln3AGdhSWd4.mp4" and meta["id"] == "Ln3AGdhSWd4"

def test_transcript_quality_json_safe():
    import json, numpy as np
    q = {"coverage": round(float(np.float32(0.91)), 3)}
    q["ok"] = bool(True and q["coverage"] >= 0.8)
    json.dumps(q)
