import json
import numpy as np
from shortrizz import watermark as W
from shortrizz.cli import _wm_flags
import argparse

def clean(monkeypatch, tmp_path):
    for k in list(__import__("os").environ):
        if k.startswith("SHORTRIZZ_WATERMARK"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("SHORTRIZZ_CONFIG", str(tmp_path / "none.json"))

def test_default_off(monkeypatch, tmp_path):
    clean(monkeypatch, tmp_path)
    assert W.settings()["watermark"] is False
    assert W.settings(None)["watermark"] is False

def test_cli_flags_default_none():
    p = argparse.ArgumentParser(); _wm_flags(p)
    assert p.parse_args([]).watermark is None
    assert p.parse_args(["--watermark"]).watermark is True
    assert p.parse_args(["--no-watermark"]).watermark is False

def test_config_env_cli_precedence(monkeypatch, tmp_path):
    clean(monkeypatch, tmp_path)
    cfg = tmp_path / "c.json"; cfg.write_text(json.dumps({"watermark": True, "watermark_position": "top-left"}))
    monkeypatch.setenv("SHORTRIZZ_CONFIG", str(cfg))
    st = W.settings(); assert st["watermark"] and st["position"] == "top-left"
    monkeypatch.setenv("SHORTRIZZ_WATERMARK", "0")
    assert W.settings()["watermark"] is False
    assert W.settings(True)["watermark"] is True
    monkeypatch.setenv("SHORTRIZZ_WATERMARK", "1")
    assert W.settings(False)["watermark"] is False

def test_overlay_stays_in_top_right_and_blends():
    st = dict(W.DEFAULTS, watermark=True)
    ov = W.Overlay(1080, 1920, st)
    y0, y1, x0, x1 = ov.box
    assert x1 <= 1080 and y0 >= 0 and y1 < 1920 * 0.2 and x0 > 540  # top-right, far above captions (0.70 H)
    fr = np.zeros((1920, 1080, 3), np.uint8)
    out = ov.apply(fr)
    assert out[y0:y1, x0:x1].max() > 100 and out[1000:, :].max() == 0

def test_plugin_param_maps_to_flag(monkeypatch, tmp_path):
    from shortrizz import hermes_tools as H
    seen = {}
    monkeypatch.setattr(H, "_start_job", lambda kind, argv, out: seen.setdefault("argv", argv) and
                        {"job_id": "x", "kind": kind, "pid": 999999, "out_dir": str(out), "log": str(out / "l"),
                         "started": 0})
    monkeypatch.setattr(H, "_wait", lambda job, w: {"status": "running"})
    H.make_shorts_tool({"source": "https://example.com/v", "out_dir": str(tmp_path)})
    assert "--watermark" not in seen["argv"] and "--no-watermark" not in seen["argv"]
    seen.clear(); H.make_shorts_tool({"source": "https://example.com/v", "out_dir": str(tmp_path), "watermark": True})
    assert "--watermark" in seen["argv"]
