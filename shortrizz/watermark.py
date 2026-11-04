"""Optional Shortrizz watermark. OFF by default for everyone.

Resolution order (first that is set wins):
  1. CLI --watermark / --no-watermark, or the plugin tool's `watermark` param
  2. env SHORTRIZZ_WATERMARK=1|0
  3. user config file ($SHORTRIZZ_CONFIG or ~/.config/shortrizz/config.json), key "watermark"
  4. default: False
Other settings (env SHORTRIZZ_WATERMARK_<KEY> or config "watermark_<key>"):
  position (top-right|top-left|bottom-right|bottom-left, default top-right: clear of the
  lower-middle captions and of the face, which the reframer centres), opacity (0.7),
  width (fraction of frame width, 0.26), text ("made with" line, default false).
"""
import json, os
from pathlib import Path
import numpy as np

ASSETS = Path(__file__).resolve().parent / "assets"
DEFAULTS = {"watermark": False, "position": "top-right", "opacity": 0.7, "width": 0.26, "text": False}
TRUE = {"1", "true", "yes", "on"}

def config_path():
    return Path(os.environ.get("SHORTRIZZ_CONFIG", Path.home() / ".config" / "shortrizz" / "config.json")).expanduser()

def _user_config():
    p = config_path()
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except (OSError, ValueError):
        return {}

def _coerce(key, v):
    if key in ("watermark", "text"):
        return v if isinstance(v, bool) else str(v).strip().lower() in TRUE
    if key in ("opacity", "width"):
        return float(v)
    return str(v)

def settings(cli=None):
    """cli: True/False/None from flags. Returns dict with 'watermark' bool and style keys."""
    cfg = _user_config()
    out = dict(DEFAULTS)
    for key in DEFAULTS:
        ck = "watermark" if key == "watermark" else f"watermark_{key}"
        ek = "SHORTRIZZ_WATERMARK" if key == "watermark" else f"SHORTRIZZ_WATERMARK_{key.upper()}"
        if ck in cfg:
            out[key] = _coerce(key, cfg[ck])
        if os.environ.get(ek, "") != "":
            out[key] = _coerce(key, os.environ[ek])
    if cli is not None:
        out["watermark"] = bool(cli)
    out["opacity"] = min(1.0, max(0.1, out["opacity"]))
    out["width"] = min(0.6, max(0.08, out["width"]))
    if out["position"] not in ("top-right", "top-left", "bottom-right", "bottom-left"):
        out["position"] = "top-right"
    return out

class Overlay:
    """Pre-scaled RGBA watermark alpha-blended onto BGR frames."""
    def __init__(self, frame_w, frame_h, st):
        import cv2
        img = cv2.imread(str(ASSETS / ("watermark-madewith.png" if st["text"] else "watermark.png")), cv2.IMREAD_UNCHANGED)
        w = int(frame_w * st["width"]); h = int(img.shape[0] * w / img.shape[1])
        img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
        self.rgb = img[:, :, :3].astype(np.float32)
        self.a = (img[:, :, 3:4].astype(np.float32) / 255.0) * st["opacity"]
        # soft shadow so a white mark stays readable on bright backgrounds
        self.sh = cv2.GaussianBlur(img[:, :, 3], (0, 0), max(1, w / 120)).astype(np.float32)[..., None] / 255.0 * 0.35 * st["opacity"]
        # platform safe zones (scaled from 1080x1920): top 288, right 192, left 120, bottom UI below y=1240
        sx, sy = frame_w / 1080, frame_h / 1920
        x = frame_w - w - int(192 * sx) if st["position"].endswith("right") else int(120 * sx)
        y = int(288 * sy) if st["position"].startswith("top") else int(1240 * sy) - h
        self.box = (y, y + h, x, x + w)

    def apply(self, fr):
        y0, y1, x0, x1 = self.box
        roi = fr[y0:y1, x0:x1].astype(np.float32)
        roi = roi * (1 - self.sh)
        roi = roi * (1 - self.a) + self.rgb * self.a
        fr = fr.copy() if not fr.flags.writeable else fr
        fr[y0:y1, x0:x1] = np.clip(roi, 0, 255).astype(np.uint8)
        return fr
