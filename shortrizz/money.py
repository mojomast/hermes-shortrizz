"""Frame 0 of every export is a finished hero, not a fade, a blink, or a black frame.

The picture may be held for a short catch-up (the hero sits a little after the
in-point) or a short picture-only cold open (the hero is later in the cut).
Audio is not delayed and no frames are inserted, so the file stays the same
length and speech stays on the clock the editor already built.

On by default. Turn it off with ``--no-money-shot``, ``SHORTRIZZ_MONEY_SHOT``
(``0``, ``false``, ``off``, ``no``), or ``"money_shot": false`` in the user
config. An explicit ``hero: true`` on a segment outranks the others. An
explicit ``hero_time`` (source seconds) outranks every segment.
"""
import math
import os

import numpy as np

from . import reframe
from .util import log

SAMPLE_FPS = 4.0
PROXY_W = 192
CATCHUP_MAX = 1.15   # hold the hero until the clock reaches it, when it is this close to the open
COLD_OPEN_SEC = 0.28  # picture-only cold open when the hero is later than that
_OFF = {"0", "false", "off", "no"}
_ON = {"1", "true", "on", "yes"}
_HAAR = None


def enabled(cli=None, environ=None):
    """CLI value, then ``SHORTRIZZ_MONEY_SHOT``, then config ``money_shot``, then on.

    ``cli`` is the ``--money-shot`` / ``--no-money-shot`` value (``True``,
    ``False``, or ``None`` when the flag was omitted). ``environ`` defaults to
    the process environment; tests pass a dict.
    """
    if cli is True:
        return True
    if cli is False:
        return False
    env = os.environ if environ is None else environ
    flag = str(env.get("SHORTRIZZ_MONEY_SHOT", "")).strip().lower()
    if flag in _OFF:
        return False
    if flag in _ON:
        return True
    from .watermark import _user_config
    cfg = _user_config()
    if "money_shot" in cfg:
        v = cfg["money_shot"]
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() not in _OFF
    return True


def meta_from_clip(clip):
    """Virality, hook time, and an optional explicit hero from a clip or segment dict."""
    clip = clip or {}
    hero_time = _float_or_none(clip.get("hero_time"))
    peak = hero_time if hero_time is not None else _float_or_none(clip.get("word_start"))
    if peak is None:
        peak = _float_or_none(clip.get("start"))
    try:
        virality = float(clip.get("virality_score") if clip.get("virality_score") is not None else clip.get("score") or 0)
    except (TypeError, ValueError):
        virality = 0.0
    return {
        "virality": virality,
        "peak_time": peak,
        "hero": clip.get("hero") is True,
        "hero_time": hero_time,
    }


def ranges_from_items(items, meta):
    """One searchable span per kept source segment, in output order."""
    meta = meta or {}
    out = []
    for it in items or []:
        if it.get("kind") != "src":
            continue
        try:
            start, end = float(it["s"]), float(it["e"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        out.append({
            "start": start, "end": end,
            "virality": meta.get("virality") or 0,
            "peak_time": meta.get("peak_time"),
            "hero": bool(meta.get("hero")),
            "hero_time": meta.get("hero_time"),
        })
    return out


def ranges_from_segments(segments):
    """Highlight segments in play order. The first one is the opening shot."""
    out = []
    for seg in segments or []:
        try:
            start, end = float(seg["start"]), float(seg["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        meta = meta_from_clip(seg)
        out.append({"start": start, "end": end, **meta})
    return out


def _float_or_none(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _haar():
    """Frontal-face cascade when this OpenCV build has one. Missing means no face bonus, not a crash."""
    global _HAAR
    if _HAAR is None:
        import cv2
        ctor = getattr(cv2, "CascadeClassifier", None)
        path = getattr(getattr(cv2, "data", None), "haarcascades", "")
        det = ctor(path + "haarcascade_frontalface_default.xml") if ctor and path else None
        _HAAR = det if det is not None and not det.empty() else False
    return _HAAR or None


def _measure(bgr, previous):
    import cv2
    h, w = bgr.shape[:2]
    tw = 160
    th = max(2, int(round(h * tw / max(1, w))))
    small = cv2.resize(bgr, (tw, th), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    luma = float(gray.mean())
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    motion = 0.0
    if previous is not None:
        ph, pw = previous.shape[:2]
        other = previous
        if (pw, ph) != (tw, th):
            other = cv2.resize(previous, (tw, th), interpolation=cv2.INTER_AREA)
        if other.ndim == 3:
            other = cv2.cvtColor(other, cv2.COLOR_BGR2GRAY)
        motion = float(np.mean(cv2.absdiff(gray, other)))
    return {"luma": luma, "sharp": sharp, "motion": motion, "std": float(gray.std())}


def _face(bgr):
    """(area 0..1, centred 0..1, blink). Haar only: this must not download YuNet."""
    import cv2
    det = _haar()
    if det is None or det.empty():
        return 0.0, 0.0, False
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    rects = det.detectMultiScale(gray, 1.15, 5, minSize=(max(16, gray.shape[1] // 12), max(16, gray.shape[0] // 12)))
    if rects is None or len(rects) == 0:
        return 0.0, 0.0, False
    h, w = gray.shape[:2]
    x, y, fw, fh = max(rects, key=lambda r: int(r[2]) * int(r[3]))
    x, y, fw, fh = int(x), int(y), int(fw), int(fh)
    area = (fw * fh) / float(max(1, w * h))
    centred = max(0.0, 1.0 - abs((x + fw / 2) / w - 0.5) * 2.0)
    ey0, ey1 = y + int(fh * 0.20), y + int(fh * 0.48)
    ch0, ch1 = y + int(fh * 0.55), y + int(fh * 0.90)
    ex0, ex1 = x + int(fw * 0.12), x + int(fw * 0.88)
    eyes, cheeks = gray[ey0:ey1, ex0:ex1], gray[ch0:ch1, ex0:ex1]
    blink = False
    if eyes.size and cheeks.size and float(cheeks.mean()) > 1:
        if float(eyes.mean()) < float(cheeks.mean()) * 0.62 and float(eyes.var()) < 140:
            blink = True
    return area, centred, blink


def score_metrics(m, face_area=0.0, centred=0.0, blink=False, virality=0.0, peak=0.0,
                  hero_segment=False, pinned=False):
    """Higher is a stronger thumbnail. The explicit flags outrank the picture score."""
    luma, sharp, motion = m["luma"], m["sharp"], m["motion"]
    if luma < 10:
        black = 7.0
    elif luma < 22:
        black = 4.0
    elif luma < 40:
        black = 1.6
    else:
        black = 0.0
    if luma < 48:
        light = luma / 48.0
    elif luma <= 200:
        light = 1.25
    else:
        light = max(0.25, 1.25 - (luma - 200) / 80.0)
    sharp_term = min(sharp / 28.0, 3.6)
    if sharp < 5:
        sharp_term -= 2.6
    elif sharp < 14:
        sharp_term -= 1.1
    motion_pen = min(motion / 14.0, 2.4)
    face_term = 0.0
    if face_area > 0:
        face_term = 2.1 + min(face_area * 8.0, 1.6) + 0.7 * centred
        if blink:
            face_term -= 2.0
    viral = 0.85 * max(0.0, min(1.0, float(virality)))
    return ((100.0 if hero_segment else 0.0)
            + (200.0 if pinned else 0.0)
            + face_term + sharp_term + light + viral + 0.45 * max(0.0, min(1.0, float(peak)))
            - black - motion_pen)


def rank_frames(samples, virality=0.0, peak_time=None, hero=False, hero_time=None):
    """Pick the strongest ``(t, bgr)`` sample. An exact tie keeps the earlier frame.

    ``hero`` marks the whole span (a planner flag). ``hero_time`` pins one
    source timestamp; the nearest sample within 0.4 s carries that pin.
    """
    if not samples:
        return None
    pin_t = None
    if hero_time is not None:
        nearest = min(samples, key=lambda s: abs(s[0] - float(hero_time)))
        if abs(nearest[0] - float(hero_time)) <= 0.4:
            pin_t = nearest[0]
    viral = max(0.0, min(1.0, float(virality) / 100.0))
    best = None
    prev = None
    for t, fr in samples:
        metrics = _measure(fr, prev)
        area, centred, blink = _face(fr)
        peak = 0.0
        if peak_time is not None:
            peak = math.exp(-((float(t) - float(peak_time)) / 0.55) ** 2)
        score = score_metrics(metrics, area, centred, blink, viral, peak,
                              hero_segment=bool(hero), pinned=(pin_t is not None and t == pin_t))
        if best is None or score > best["score"]:
            best = {"t": float(t), "score": float(score), "luma": metrics["luma"], "sharp": metrics["sharp"],
                    "blink": blink, "hero": bool(hero), "pinned": pin_t is not None and t == pin_t}
        prev = fr
    return best


def sample_range(src, start, end, sw, sh, fps=SAMPLE_FPS):
    """Small BGR frames across ``[start, end]`` at ``fps``. Source pixels, not the reframed output."""
    dur = float(end) - float(start)
    sw, sh = int(sw), int(sh)
    if dur <= 0.04 or sw < 2 or sh < 2:
        return []
    width = PROXY_W if sw >= PROXY_W else sw - sw % 2
    width = max(2, int(width))
    height = max(2, int(round(sh * width / float(sw) / 2) * 2))
    out = []
    gen = reframe.read_frames(src, float(start), dur, width, height, fps)
    try:
        for k, fr in enumerate(gen):
            out.append((float(start) + k / float(fps), fr))
    finally:
        close = getattr(gen, "close", None)
        if close:
            close()
    return out


def select_ranges(src, ranges, sw, sh):
    """Strongest sample across ``ranges``. Earlier frames win an exact tie, and an earlier range does too."""
    best = None
    for i, r in enumerate(ranges or []):
        samples = sample_range(src, r["start"], r["end"], sw, sh)
        picked = rank_frames(samples, virality=r.get("virality") or 0, peak_time=r.get("peak_time"),
                             hero=bool(r.get("hero")), hero_time=r.get("hero_time"))
        if picked is None:
            continue
        picked["range_index"] = i
        if best is None or picked["score"] > best["score"]:
            best = picked
    return best


def hold_plan(best, ranges, fps):
    """How long to show ``best`` at the head of the opening range. 0 frames when it is already frame 0."""
    fps = float(fps or 30.0) or 30.0
    empty = {"enabled": True, "reason": "empty", "hold_sec": 0.0, "hold_frames": 0,
             "t": None, "score": 0.0, "range_index": None, "luma": 0.0, "sharp": 0.0}
    if not best or not ranges:
        return empty
    opening = ranges[0]
    opening_dur = max(0.0, float(opening["end"]) - float(opening["start"]))
    in_opening = best.get("range_index", 0) == 0
    delta = max(0.0, float(best["t"]) - float(opening["start"]))
    frame = 1.0 / fps
    if in_opening and delta <= frame * 0.75:
        hold, reason = 0.0, "already-open"
    elif in_opening and delta <= CATCHUP_MAX:
        hold, reason = min(delta, opening_dur), "catch-up"
    else:
        hold, reason = (min(COLD_OPEN_SEC, opening_dur) if opening_dur else COLD_OPEN_SEC), "cold-open"
    if hold <= frame * 0.75:
        hold, reason = 0.0, "already-open"
        hold_frames = 0
    else:
        hold_frames = max(1, int(round(hold * fps)))
    return {
        "enabled": True,
        "reason": reason,
        "hold_sec": round(hold, 3) if hold_frames else 0.0,
        "hold_frames": hold_frames,
        "t": round(float(best["t"]), 3),
        "score": round(float(best["score"]), 3),
        "range_index": best.get("range_index"),
        "luma": round(float(best.get("luma") or 0), 2),
        "sharp": round(float(best.get("sharp") or 0), 2),
    }


def describe(plan, on=True):
    """JSON-safe summary stored on the render result. No image bytes."""
    if not on:
        return {"enabled": False, "reason": "disabled", "t": None, "hold_sec": 0.0, "score": 0.0}
    plan = plan or {}
    return {
        "enabled": bool(plan.get("enabled", True)),
        "reason": plan.get("reason") or "empty",
        "t": plan.get("t"),
        "hold_sec": float(plan.get("hold_sec") or 0),
        "score": float(plan.get("score") or 0),
    }


def choose(src, ranges, sw, sh, fps):
    """Select the hero and the hold. Logs one line. Does not decode a full-size frame."""
    if not ranges:
        plan = hold_plan(None, ranges, fps)
        return plan
    best = select_ranges(src, ranges, sw, sh)
    plan = hold_plan(best, ranges, fps)
    if plan.get("t") is not None:
        log(f"  money shot {plan['reason']} at {plan['t']:.2f}s  score {plan['score']:.2f}  "
            f"hold {plan['hold_sec']:.2f}s")
    return plan


def output_frame(src, t, sw, sh, fps, analysis, clip_start, out_w=1080, out_h=1920):
    """One reframed BGR frame at source time ``t``, matching what the encoder would draw."""
    fps = float(fps or 30.0) or 30.0
    gen = reframe.render_frames(
        src, float(t), max(0.08, 1.5 / fps), int(sw), int(sh), fps,
        out_w=out_w, out_h=out_h, analysis=analysis,
        t_offset=max(0.0, float(t) - float(clip_start)))
    try:
        return next(gen, None)
    finally:
        close = getattr(gen, "close", None)
        if close:
            close()


def fitted_still(src, t, sw, sh, out_w, out_h):
    """One source frame fitted into ``out_w`` x ``out_h`` the way highlights pads 16:9. Never upscales past 1:1."""
    import cv2
    gen = reframe.read_frames(src, float(t), 0.12, int(sw), int(sh), None)
    try:
        fr = next(gen, None)
    finally:
        close = getattr(gen, "close", None)
        if close:
            close()
    if fr is None:
        return None
    h, w = fr.shape[:2]
    out_w, out_h = int(out_w), int(out_h)
    if w == out_w and h == out_h:
        return np.ascontiguousarray(fr)
    scale = min(out_w / float(w), out_h / float(h), 1.0)
    nw = max(2, int(w * scale) // 2 * 2)
    nh = max(2, int(h * scale) // 2 * 2)
    resized = cv2.resize(fr, (nw, nh), interpolation=cv2.INTER_AREA)
    if nw == out_w and nh == out_h:
        return np.ascontiguousarray(resized)
    canvas = np.zeros((out_h, out_w, 3), np.uint8)
    y = max(0, (out_h - nh) // 2)
    x = max(0, (out_w - nw) // 2)
    canvas[y:y + nh, x:x + nw] = resized
    return canvas


def write_png(fr, path):
    import cv2
    if fr is None or not cv2.imwrite(str(path), fr):
        raise RuntimeError(f"could not write hero still {path}")
