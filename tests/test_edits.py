import json
from shortrizz import edits as E, captions, cli, hermes_tools as H
from shortrizz import render as R

def W(spec):
    """spec: list of (word, start, end)."""
    return [{"w": w, "s": s, "e": e} for w, s, e in spec]

WORDS = W([("So", 0.0, 0.2), ("I", 0.25, 0.3), ("I", 0.35, 0.4), ("think", 0.45, 0.7), ("um", 1.2, 1.4),
           ("we", 2.4, 2.6), ("did", 2.65, 2.9), ("not", 2.95, 3.1), ("lose", 3.15, 3.4), ("100", 3.45, 3.8),
           ("dollars.", 3.85, 4.3), ("Then", 5.5, 5.7), ("we", 5.75, 5.9), ("grew", 5.95, 6.3)])
A, B = 0, len(WORDS) - 1

def test_rule_drops_find_filler_and_repeat():
    d = E.rule_drops(WORDS, A, B)
    assert {"w0": 1, "w1": 1, "reason": "repeat"} in d
    assert any(x["reason"] == "filler" and x["w0"] == 4 for x in d)

def test_validate_protects_negations_numbers_and_caps_removal():
    plan = {"drop": [{"w0": 7, "w1": 7}, {"w0": 9, "w1": 9}, {"w0": 0, "w1": B}, {"w0": 11, "w1": 13}],
            "hook_text": "This one weird trick changes everything forever", "emphasis": [9, 10, 999],
            "cold_open": {"w0": 5, "w1": 10}, "loop_end": 2}
    v = E.validate(plan, WORDS, A, B, E.rule_drops(WORDS, A, B))
    gone = {k for d in v["drop"] for k in range(d["w0"], d["w1"] + 1)}
    assert 7 not in gone and 9 not in gone                    # "not", "100" survive
    assert len(gone) <= max(1, int(len(WORDS) * E.MAX_DROP_FRAC))
    assert len(v["hook_text"].split()) <= 6
    assert all(A <= w <= B for w in v["emphasis"])
    assert v["loop_end"] is None                               # too early to be an ending

def test_gap_tightening_and_word_boundaries():
    pol = E.PRESETS["normal"]
    segs, cuts = E.build_segments(WORDS, A, B, [{"w0": 4, "w1": 4}], pol)
    kept = sum(e - s for s, e in segs)
    assert kept < WORDS[B]["e"] - WORDS[A]["s"] - 2.0          # the 1-2 s pauses are gone
    for s, e in segs:                                          # no cut lands inside a word
        for w in WORDS:
            assert not (w["s"] + 0.01 < s < w["e"] - 0.01) and not (w["s"] + 0.01 < e < w["e"] - 0.01)
    out, total = E.remap(WORDS, [i for i in range(A, B + 1) if i != 4], segs)
    assert abs(total - kept) < 1e-6 and all(b["s"] >= a["s"] for a, b in zip(out, out[1:]))
    gaps = [b["s"] - a["e"] for a, b in zip(out, out[1:])]
    assert max(gaps) <= pol["max_gap"] + 0.01                  # captions retimed to the new timeline

def test_edit_off_is_one_continuous_range():
    segs, cuts = E.build_segments(WORDS, A, B, [], None)
    assert len(segs) == 1 and not cuts

def test_options_defaults_and_optins(monkeypatch, tmp_path):
    monkeypatch.setenv("SHORTRIZZ_CONFIG", str(tmp_path / "none.json"))
    o = E.options()
    assert o["edit"] == "normal" and o["hook_text"] and o["loudnorm"] and o["llm_edits"]
    assert not any(o[k] for k in E.OPT_INS)                   # every opt-in is off by default
    (tmp_path / "c.json").write_text(json.dumps({"zoom": True}))
    monkeypatch.setenv("SHORTRIZZ_CONFIG", str(tmp_path / "c.json"))
    assert E.options()["zoom"] and not E.options({"zoom": False})["zoom"]

def test_cli_all_fx_and_plugin_params():
    import argparse
    p = argparse.ArgumentParser(); cli._edit_flags(p)
    d = cli._edits(p.parse_args(["--all-fx", "--no-hook-text"]))
    assert all(d[k] for k in E.OPT_INS) and d["hook_text"] is False
    argv = H._edit_argv({"zoom": True, "loop": True, "deeper_edits": False, "edit": "light"})
    assert argv == ["--edit", "light", "--no-edits", "--zoom", "--loop"]

def test_safe_zones_for_captions_and_hook():
    st = captions.load_style("hormozi")
    ass = captions.build_ass(W([("hello", 0.1, 0.4), ("there", 0.5, 0.9)]), 0, 2, st,
                             overlays=[{"text": "Hook Text Here", "t0": 0, "t1": 2.8}])
    import re
    ys = [int(y) for y in re.findall(r"\\pos\(\d+,(\d+)\)", ass)]
    assert ys and all(captions.SAFE_TOP < y < captions.SAFE_BOTTOM_Y for y in ys)
    assert "Hook" in ass and "HOOK TEXT HERE" in ass

def test_zoom_schedule():
    fx = {"bases": [(0, 1, 1.0), (1, 2, 1.08)], "punches": [(1.5, 2.0)]}
    assert R.zoom_at(0.5, fx) == 1.0 and R.zoom_at(1.2, fx) == 1.08 and R.zoom_at(1.9, fx) > 1.1
