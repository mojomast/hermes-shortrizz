import json, subprocess
import numpy as np
from pathlib import Path
from .util import run, log
from . import money, reframe

def probe(src):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=width,height,r_frame_rate", "-of", "json", str(src)], capture_output=True, text=True).stdout
    s = json.loads(out)["streams"][0]
    n, d = s["r_frame_rate"].split("/")
    return s["width"], s["height"], float(n) / float(d)

def _money_ranges(items, clip, start, end):
    meta = money.meta_from_clip(clip)
    ranges = money.ranges_from_items(items, meta) if items else []
    if ranges:
        return ranges
    if end > start:
        return [{"start": float(start), "end": float(end), **meta}]
    return []


def _money_open(src, start, end, sw, sh, fps, analysis, items, clip, money_shot, out_w=1080, out_h=1920):
    """``(hero_frame or None, json summary)``. The hero replaces the first hold frames. Audio is untouched."""
    if not money.enabled(money_shot):
        return None, money.describe(None, on=False)
    ranges = _money_ranges(items, clip, start, end)
    plan = money.choose(src, ranges, sw, sh, fps)
    frame = None
    if plan.get("hold_frames") and plan.get("t") is not None:
        frame = money.output_frame(src, plan["t"], sw, sh, fps, analysis, start, out_w, out_h)
        if frame is None:
            plan = {**plan, "hold_frames": 0, "hold_sec": 0.0, "reason": "no-frame"}
    summary = money.describe(plan, on=True)
    summary["hold_frames"] = int(plan.get("hold_frames") or 0) if frame is not None else 0
    return frame, summary


def render_clip(src, start, end, ass_path, out_path, reframe_mode="auto", crf=20, preset="medium", watermark=None,
                money_shot=None, clip=None):
    sw, sh, fps = probe(src)
    fps = min(fps, 60.0)
    dur = end - start
    plan = reframe.make_plan(src, start, dur, sw, sh, reframe_mode)
    ass = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    hero, shot = _money_open(src, start, end, sw, sh, fps, plan, None, clip, money_shot)
    hold_n = int(shot.get("hold_frames") or 0)
    enc = subprocess.Popen([
        "ffmpeg", "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "1080x1920", "-r", f"{fps}", "-i", "-",
        "-ss", f"{start}", "-t", f"{dur}", "-i", str(src),
        "-map", "0:v", "-map", "1:a?", "-vf", f"ass='{ass}'",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-movflags", "+faststart", "-shortest", str(out_path)], stdin=subprocess.PIPE)
    n = 0
    wm = None
    if watermark and watermark.get("watermark"):
        from .watermark import Overlay
        wm = Overlay(1080, 1920, watermark)
    for fr in reframe.render_frames(src, start, dur, sw, sh, fps, analysis=plan):
        if hero is not None and n < hold_n:
            fr = np.array(hero, copy=True)
        if wm is not None:
            fr = wm.apply(np.array(fr))
        enc.stdin.write(np.ascontiguousarray(fr).tobytes()); n += 1
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError(f"ffmpeg encode failed for {out_path}")
    audio = check_audio(out_path)
    shot.pop("hold_frames", None)
    return {"audio_lufs": audio, "frames": n, "watermark": wm is not None,
            "reframe": "letterbox" if plan is None else ("center" if len(plan[0]) == 1 else "face"),
            "money_shot": shot}

def thumbnail(mp4, out_jpg, t=1.0):
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t}", "-i", str(mp4), "-frames:v", "1", "-vf", "scale=360:-2",
         "-q:v", "3", str(out_jpg)])


def still_frame(src, t, out_jpg, width=360):
    """Small JPEG of the source at ``t`` seconds. ``-ss`` is placed before ``-i``."""
    out_jpg = Path(out_jpg)
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, float(t)):.3f}", "-i", str(src),
         "-frames:v", "1", "-vf", f"scale={int(width)}:-2", "-q:v", "5", str(out_jpg)])

# ---------------------------------------------------------------- deeper edits renderer
def _frames_exact(gen, n, fallback):
    last = fallback
    k = 0
    for fr in gen:
        if k >= n:
            break
        last = fr; k += 1
        yield fr
    if hasattr(gen, "close"):
        gen.close()
    while k < n:  # pad to the exact count so audio and video stay in sync over many cuts
        k += 1
        yield last

def _ease(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3

def zoom_at(t, fx):
    """Output-time zoom: alternating 1.0/1.08 base per jump-cut segment + punch-ins on emphasis words."""
    z = 1.0
    for (t0, t1, base) in fx.get("bases", []):
        if t0 <= t < t1:
            z = base; break
    for (t0, t1) in fx.get("punches", []):
        if t0 <= t < t1:
            z = max(z, 1.0 + 0.12 * _ease((t - t0) / 0.15))
    for (t0, t1) in fx.get("drift", []):  # slow Ken Burns on held frames
        if t0 <= t < t1:
            z = 1.0 + 0.04 * (t - t0) / max(0.1, t1 - t0)
    return z

def apply_zoom(fr, z, cy=0.42):
    if z <= 1.001:
        return fr
    import cv2
    h, w = fr.shape[:2]
    cw, ch = int(w / z), int(h / z)
    x0 = (w - cw) // 2
    y0 = int(min(h - ch, max(0, cy * h - ch * cy)))
    return cv2.resize(fr[y0:y0 + ch, x0:x0 + cw], (w, h), interpolation=cv2.INTER_LINEAR)

class EmojiPop:
    """Colour emoji rendered from the system Noto Color Emoji font (OFL) and popped above the captions."""
    FONT = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
    def __init__(self, items, y_center, size=150, x_center=250, places=None):
        from PIL import Image, ImageDraw, ImageFont
        self.items = []
        try:
            font = ImageFont.truetype(self.FONT, 109)
        except OSError:
            log("  emoji font missing; skipping emoji pops"); return
        for t0, ch in items:
            im = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((80, 80), ch, font=font, embedded_color=True, anchor="mm")
            bb = im.getbbox()
            if not bb:
                continue
            im = im.crop(bb).resize((size, int(size * (bb[3] - bb[1]) / max(1, bb[2] - bb[0]))))
            arr = np.array(im)[:, :, [2, 1, 0, 3]].astype(np.float32)
            self.items.append((t0, arr))
        self.y, self.x = y_center, x_center
        self.places = places or {}  # t0 -> (x, y) chosen off the face

    def apply(self, fr, t):
        import cv2
        for t0, arr in self.items:
            if t0 in self.places and self.places[t0] is None:
                continue
            if t0 <= t < t0 + 1.2:
                s = 0.6 + 0.4 * _ease((t - t0) / 0.12)
                pl = self.places.get(t0) or (self.x, self.y, arr.shape[1])
                s *= (pl[2] if len(pl) > 2 else arr.shape[1]) / arr.shape[1]
                a = cv2.resize(arr, (max(2, int(arr.shape[1] * s)), max(2, int(arr.shape[0] * s))))
                h, w = a.shape[:2]
                px, py = pl[0], pl[1]
                x0, y0 = int(px - w / 2), int(py - h / 2)
                roi = fr[y0:y0 + h, x0:x0 + w].astype(np.float32)
                al = a[:, :, 3:4] / 255.0
                fr = fr.copy() if not fr.flags.writeable else fr
                fr[y0:y0 + h, x0:x0 + w] = np.clip(roi * (1 - al) + a[:, :, :3] * al, 0, 255).astype(np.uint8)
        return fr

def _audio_graph(items, xfade, sfx, loop, si=0, base=0.0):
    """items: [{kind:src,s,e}|{kind:hold,dur,wav}] -> (extra inputs, filter_complex, out label)."""
    inputs, chains, labels = [], [], []
    d = xfade / 1000.0
    nsrc = [k for k, it in enumerate(items) if it["kind"] == "src"]
    srcin = {k: si + j for j, k in enumerate(nsrc)}   # one input per kept segment (see render_edit)
    off = si + max(1, len(nsrc)) - 1                          # extra inputs come after the source copies
    fmt = "aformat=sample_rates=48000:channel_layouts=stereo"
    for k, it in enumerate(items):
        first, last = k == 0, k == len(items) - 1
        if it["kind"] == "src":
            s = it["s"] - (0 if first else d / 2); e = it["e"] + (0 if last else d / 2)
            chains.append(f"[{srcin[k]}:a]atrim={max(0, s - base):.3f}:{e - base:.3f},asetpts=PTS-STARTPTS,{fmt}[a{k}]")
        else:
            dur = it["dur"] + (0 if first or last else d)
            if it.get("wav"):
                inputs.append(it["wav"]); n = len(inputs) + off
                chains.append(f"[{n}:a]{fmt},apad,atrim=0:{dur:.3f},asetpts=PTS-STARTPTS[a{k}]")
            else:
                chains.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.3f}[a{k}]")
        labels.append(f"a{k}")
    cur = labels[0]
    for k, lab in enumerate(labels[1:], 1):
        chains.append(f"[{cur}][{lab}]acrossfade=d={d:.3f}:c1=qsin:c2=qsin[x{k}]"); cur = f"x{k}"
    if sfx:
        mix = [f"[{cur}]"]
        for j, (t, wav) in enumerate(sfx):
            inputs.append(wav); n = len(inputs) + off
            chains.append(f"[{n}:a]{fmt},volume=-20dB,adelay={int(t * 1000)}|{int(t * 1000)}[s{j}]")
            mix.append(f"[s{j}]")
        chains.append("".join(mix) + f"amix=inputs={len(mix)}:duration=first:normalize=0[mx]"); cur = "mx"
    if loop:  # end on the beat without a fade-to-black or a dangling breath
        chains.append(f"[{cur}]areverse,afade=t=in:d=0.06,areverse[lp]"); cur = "lp"
    chains.append(f"[{cur}]highpass=f=80[pre]")
    return inputs, chains, "pre"

def check_audio(path, silent_lufs=-50.0):
    """Final output check: the file must have an audio stream with real sound in it.
    Raises if the stream is missing or silent; returns the integrated loudness (LUFS)."""
    import re as _re
    has = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    if not has:
        raise RuntimeError(f"no audio stream in {path}")
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0",
                          "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
    m = _re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    lufs = float(m[-1]) if m else -70.0
    if lufs <= silent_lufs:
        raise RuntimeError(f"silent audio in {path} ({lufs} LUFS)")
    return lufs

def _measure_loudness(src, inputs, chains, label):
    import re as _re
    g = ";".join(chains) + f";[{label}]loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json[o]"
    cmd = ["ffmpeg", "-hide_banner", "-nostats", *src]
    for x in inputs:
        cmd += ["-i", str(x)]
    cmd += ["-filter_complex", g, "-map", "[o]", "-f", "null", "-"]
    err = subprocess.run(cmd, capture_output=True, text=True).stderr
    m = _re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", err, _re.S)
    return json.loads(m.group(0)) if m else None

def prepare(src, clip_start, clip_end, reframe_mode="auto"):
    sw, sh, fps = probe(src)
    fps = min(fps, 30.0) if fps > 31 else fps  # 60 fps sources render at 30: half the CPU, fine for Shorts
    plan = reframe.make_plan(src, clip_start, clip_end - clip_start, sw, sh, reframe_mode)
    return {"src": src, "sw": sw, "sh": sh, "fps": fps, "plan": plan, "clip_start": clip_start}

def _frame_at(ctx, t_src, n=1):
    g = reframe.render_frames(ctx["src"], t_src, 0.05 + n / ctx["fps"], ctx["sw"], ctx["sh"], ctx["fps"],
                              analysis=ctx["plan"], t_offset=t_src - ctx["clip_start"])
    out = []
    for fr in g:
        out.append(fr)
        if len(out) >= n:
            break
    g.close()
    return out

_DET = {}
def faces_in(fr):
    """Face boxes (x0, y0, x1, y1) in 1080x1920 output coords, detected at 1/3 scale."""
    import cv2
    if "d" not in _DET:
        _DET["d"] = reframe.Detector(360, 640)
    small = cv2.resize(fr, (360, 640), interpolation=cv2.INTER_AREA)
    return [(x * 3, y * 3, (x + w) * 3, (y + h) * 3) for x, y, w, h in _DET["d"](small) if w > 18]

def sample_frames(ctx, t0, t1, fps=2.0):
    """One decode pass: reframed output frames every 1/fps s over [t0, t1] (source time)."""
    import cv2
    sw, sh, plan = ctx["sw"], ctx["sh"], ctx["plan"]
    out = []
    crop_w = int(round(sh * 1080 / 1920 / 2) * 2)
    for k, fr in enumerate(reframe.read_frames(ctx["src"], t0, t1 - t0, sw, sh, fps)):
        t = t0 + k / fps
        if plan is None or crop_w >= sw:
            o = reframe.letterbox(fr, 1080, 1920)
        else:
            xs, afps = plan
            j = min(len(xs) - 1, max(0, int((t - ctx["clip_start"]) * afps)))
            x = int(round(xs[j]))
            o = cv2.resize(fr[:, x:x + crop_w], (1080, 1920), interpolation=cv2.INTER_AREA)
        out.append((t, o))
    return out

def _zoom_box(b, z, cy=0.42, w=1080, h=1920):
    if z <= 1.001:
        return b
    x0 = (w - w / z) / 2
    y0 = min(h - h / z, max(0, cy * h - (h / z) * cy))
    return ((b[0] - x0) * z, (b[1] - y0) * z, (b[2] - x0) * z, (b[3] - y0) * z)

def _ensure_samples(ctx, items):
    if "samples" not in ctx:
        srcs = [it for it in items if it["kind"] == "src"]
        t0 = min([it["s"] for it in srcs] + [it.get("window", (it.get("at", 1e9),))[0] for it in items if it["kind"] == "hold"])
        t1 = max([it["e"] for it in srcs] + [it.get("window", (0, it.get("at", 0)))[-1] for it in items if it["kind"] == "hold"])
        sm = sample_frames(ctx, max(0.0, t0), t1 + 0.5)
        ctx["samples"] = [(t, fr, faces_in(fr)) for t, fr in sm]
    return ctx["samples"]

def _nearest(samples, t):
    return min(samples, key=lambda x: abs(x[0] - t)) if samples else None

def best_hold_frame(ctx, t0, t1, items=()):
    """Steadiest, sharpest sample with a clear, centred face in [t0, t1] (avoids blinks/motion/odd angles)."""
    import cv2
    sm = _ensure_samples(ctx, items)
    best, best_s = None, -1e9
    for i, (t, fr, fs) in enumerate(sm):
        if not (t0 <= t <= t1):
            continue
        g = cv2.cvtColor(cv2.resize(fr, (360, 640)), cv2.COLOR_BGR2GRAY)
        sharp = cv2.Laplacian(g, cv2.CV_64F).var()
        nb = sm[i + 1][1] if i + 1 < len(sm) else sm[i - 1][1]
        motion = float(np.mean(cv2.absdiff(g, cv2.cvtColor(cv2.resize(nb, (360, 640)), cv2.COLOR_BGR2GRAY))))
        face = max(((b[2] - b[0]) * (b[3] - b[1]) for b in fs), default=0) / (1080 * 1920)
        centred = max((1 - abs((b[0] + b[2]) / 2 - 540) / 540 for b in fs), default=0)
        score = (3.0 if fs else 0) + min(face * 20, 2) + centred + min(sharp / 300, 1.5) - motion / 4
        if score > best_s:
            best, best_s = t, score
    if best is None:
        return None
    fr = _frame_at(ctx, best)  # full-quality frame for the hold
    return (best, fr[0]) if fr else None

def face_track(ctx, items, fx, step=0.5):
    """[(t_out, [boxes])] every `step` s of output time (after zoom), from one 2 fps decode pass."""
    sm = _ensure_samples(ctx, items)
    out, t_out = [], 0.0
    for it in items:
        dur = it["dur"] if it["kind"] == "hold" else it["e"] - it["s"]
        if it["kind"] == "hold":
            fr = it.get("frame")
            boxes = faces_in(fr) if fr is not None else (_nearest(sm, it["at"]) or (0, 0, []))[2]
            out.append((t_out, [_zoom_box(b, zoom_at(t_out, fx)) for b in boxes]))
            out.append((t_out + dur, out[-1][1]))
        else:
            k = 0.0
            while k < dur:
                n = _nearest(sm, it["s"] + k)
                if n:
                    z = zoom_at(t_out + k, fx)
                    out.append((t_out + k, [_zoom_box(b, z) for b in n[2]]))
                k += step
        t_out += dur
    return out

def pick_holds(ctx, items):
    for it in items:
        if it["kind"] == "hold" and it.get("window") and it.get("frame") is None:
            b = best_hold_frame(ctx, *it["window"], items=items)
            if b:
                it["at"], it["frame"] = b

def render_edit(src, clip_start, clip_end, items, ass_path, out_path, fx, reframe_mode="auto", crf=20,
                preset="medium", watermark=None, loudnorm=True, ctx=None, money_shot=None, clip=None):
    """One encode: Python-reframed frames for every kept segment (+ holds), one audio graph with
    crossfades/SFX, two-pass loudnorm, ASS captions/hook text burned in."""
    ctx = ctx or prepare(src, clip_start, clip_end, reframe_mode)
    sw, sh, fps, plan = ctx["sw"], ctx["sh"], ctx["fps"], ctx["plan"]
    hero, shot = _money_open(src, clip_start, clip_end, sw, sh, fps, plan, items, clip, money_shot)
    hold_n = int(shot.get("hold_frames") or 0)
    base = max(0.0, min(it.get("s", clip_start) for it in items) - 1.0)
    span = max(it.get("e", clip_end) for it in items) - base + 1.0
    # one demuxed copy of the source span per kept segment: an asplit feeding acrossfade goes silent
    # when a later segment (the cold open) plays before an earlier one
    nseg = max(1, sum(1 for it in items if it["kind"] == "src"))
    sin = ["-ss", f"{base:.3f}", "-t", f"{span:.3f}", "-i", str(src)] * nseg
    ag = lambda si: _audio_graph(items, fx.get("xfade_ms", 15), fx.get("sfx") or [], fx.get("loop"), si, base)
    inputs, chains, alab = ag(1)
    if loudnorm:
        m = _measure_loudness(sin, *ag(0))
        if m and m.get("input_i") not in (None, "-inf"):
            ln = (f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
                  f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
        else:
            ln = "loudnorm=I=-14:TP=-1.5:LRA=11"
        chains = chains + [f"[{alab}]{ln},aresample=48000[aout]"]
    else:
        chains = chains + [f"[{alab}]anull[aout]"]
    ass = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "1080x1920", "-r", f"{fps}",
           "-i", "-", *sin]
    for x in inputs:
        cmd += ["-i", str(x)]
    # inputs are shifted by one because stdin video is input 0
    graph = ";".join(chains)
    graph += f";[0:v]ass='{ass}'[vout]"
    cmd += ["-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(out_path)]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    wm = None
    if watermark and watermark.get("watermark"):
        from .watermark import Overlay
        wm = Overlay(1080, 1920, watermark)
    emo = EmojiPop(fx.get("emoji") or [], fx.get("emoji_y", 660), places=fx.get("emoji_places")) if fx.get("emoji") else None
    t_out, nfr, last = 0.0, 0, None
    for it in items:
        k0 = round(t_out * fps)
        dur = it["dur"] if it["kind"] == "hold" else it["e"] - it["s"]
        t_out += dur
        n = round(t_out * fps) - k0
        if it["kind"] == "src":
            gen = reframe.render_frames(src, it["s"], dur + 0.2, sw, sh, fps, analysis=plan,
                                        t_offset=it["s"] - clip_start)
            frames = _frames_exact(gen, n, last if last is not None else np.zeros((1920, 1080, 3), np.uint8))
        else:
            if it.get("frame") is None:
                g = reframe.render_frames(src, it["at"], 0.2, sw, sh, fps, analysis=plan,
                                          t_offset=it["at"] - clip_start)
                it["frame"] = next(g, last)
                g.close()  # stop the reader ffmpeg instead of leaving it blocked on a full pipe
            frames = (it["frame"] for _ in range(n))
        for fr in frames:
            t = nfr / fps
            if hero is not None and nfr < hold_n:
                fr = np.array(hero, copy=True)
            fr = apply_zoom(np.ascontiguousarray(fr), zoom_at(t, fx))
            last = fr
            if emo is not None:
                fr = emo.apply(np.array(fr), t)
            if wm is not None:
                fr = wm.apply(np.array(fr))
            enc.stdin.write(np.ascontiguousarray(fr).tobytes()); nfr += 1
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError(f"ffmpeg encode failed for {out_path}")
    audio = check_audio(out_path)
    shot.pop("hold_frames", None)
    return {"audio_lufs": audio, "frames": nfr, "duration": round(nfr / fps, 2), "watermark": wm is not None,
            "reframe": "letterbox" if plan is None else ("center" if len(plan[0]) == 1 else "face"),
            "money_shot": shot}
