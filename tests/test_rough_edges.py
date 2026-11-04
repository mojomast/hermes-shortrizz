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
    opts = {**E.DEFAULTS, **{k: True for k in E.OPT_INS}, "loop": False, "max_len": 35, "min_len": 20}
    edl = {"gap_policy": E.PRESETS["normal"], "drop": [], "intro": "a narrator line", "cta": "Follow",
           "cold_open": {"w0": 20, "w1": 27, "text": "x"}, "hook_text": "Hook", "emphasis": [], "emoji": []}
    items, cw, ovl, fx, rep, total = E.assemble("x.mp4", ws, clip, edl, opts, captions.load_style("hormozi"),
                                                tmp_path, "c")
    assert total <= 35.05 and total <= clip["end"] - clip["start"]
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
    x, y = places[1.0]
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
