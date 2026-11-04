import json, subprocess
import numpy as np
from pathlib import Path
from .util import run, log
from . import reframe

def probe(src):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=width,height,r_frame_rate", "-of", "json", str(src)], capture_output=True, text=True).stdout
    s = json.loads(out)["streams"][0]
    n, d = s["r_frame_rate"].split("/")
    return s["width"], s["height"], float(n) / float(d)

def render_clip(src, start, end, ass_path, out_path, reframe_mode="auto", crf=20, preset="medium", watermark=None):
    sw, sh, fps = probe(src)
    fps = min(fps, 60.0)
    dur = end - start
    plan = reframe.make_plan(src, start, dur, sw, sh, reframe_mode)
    ass = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
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
        if wm is not None:
            fr = wm.apply(np.array(fr))
        enc.stdin.write(fr.tobytes()); n += 1
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError(f"ffmpeg encode failed for {out_path}")
    return {"frames": n, "watermark": wm is not None, "reframe": "letterbox" if plan is None else ("center" if len(plan[0]) == 1 else "face")}

def thumbnail(mp4, out_jpg, t=1.0):
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t}", "-i", str(mp4), "-frames:v", "1", "-vf", "scale=360:-2",
         "-q:v", "3", str(out_jpg)])

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
    def __init__(self, items, y_center, size=150, x_center=250):
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

    def apply(self, fr, t):
        import cv2
        for t0, arr in self.items:
            if t0 <= t < t0 + 1.2:
                s = 0.6 + 0.4 * _ease((t - t0) / 0.12)
                a = cv2.resize(arr, (max(2, int(arr.shape[1] * s)), max(2, int(arr.shape[0] * s))))
                h, w = a.shape[:2]
                x0, y0 = int(self.x - w / 2), int(self.y - h / 2)
                roi = fr[y0:y0 + h, x0:x0 + w].astype(np.float32)
                al = a[:, :, 3:4] / 255.0
                fr = fr.copy() if not fr.flags.writeable else fr
                fr[y0:y0 + h, x0:x0 + w] = np.clip(roi * (1 - al) + a[:, :, :3] * al, 0, 255).astype(np.uint8)
        return fr

def _audio_graph(items, xfade, sfx, loop, si=0, base=0.0):
    """items: [{kind:src,s,e}|{kind:hold,dur,wav}] -> (extra inputs, filter_complex, out label)."""
    inputs, chains, labels = [], [], []
    d = xfade / 1000.0
    fmt = "aformat=sample_rates=48000:channel_layouts=stereo"
    for k, it in enumerate(items):
        first, last = k == 0, k == len(items) - 1
        if it["kind"] == "src":
            s = it["s"] - (0 if first else d / 2); e = it["e"] + (0 if last else d / 2)
            chains.append(f"[src{k}]atrim={max(0, s - base):.3f}:{e - base:.3f},asetpts=PTS-STARTPTS,{fmt}[a{k}]")
        else:
            dur = it["dur"] + (0 if first or last else d)
            if it.get("wav"):
                inputs.append(it["wav"]); n = len(inputs) + si
                chains.append(f"[{n}:a]{fmt},apad,atrim=0:{dur:.3f},asetpts=PTS-STARTPTS[a{k}]")
            else:
                chains.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.3f}[a{k}]")
        labels.append(f"a{k}")
    nsrc = [k for k, it in enumerate(items) if it["kind"] == "src"]
    chains.insert(0, f"[{si}:a]asplit={len(nsrc)}" + "".join(f"[src{k}]" for k in nsrc))
    cur = labels[0]
    for k, lab in enumerate(labels[1:], 1):
        chains.append(f"[{cur}][{lab}]acrossfade=d={d:.3f}:c1=qsin:c2=qsin[x{k}]"); cur = f"x{k}"
    if sfx:
        mix = [f"[{cur}]"]
        for j, (t, wav) in enumerate(sfx):
            inputs.append(wav); n = len(inputs) + si
            chains.append(f"[{n}:a]{fmt},volume=-20dB,adelay={int(t * 1000)}|{int(t * 1000)}[s{j}]")
            mix.append(f"[s{j}]")
        chains.append("".join(mix) + f"amix=inputs={len(mix)}:duration=first:normalize=0[mx]"); cur = "mx"
    if loop:  # end on the beat without a fade-to-black or a dangling breath
        chains.append(f"[{cur}]areverse,afade=t=in:d=0.06,areverse[lp]"); cur = "lp"
    chains.append(f"[{cur}]highpass=f=80[pre]")
    return inputs, chains, "pre"

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

def render_edit(src, clip_start, clip_end, items, ass_path, out_path, fx, reframe_mode="auto", crf=20,
                preset="medium", watermark=None, loudnorm=True):
    """One encode: Python-reframed frames for every kept segment (+ holds), one audio graph with
    crossfades/SFX, two-pass loudnorm, ASS captions/hook text burned in."""
    sw, sh, fps = probe(src)
    fps = min(fps, 60.0)
    plan = reframe.make_plan(src, clip_start, clip_end - clip_start, sw, sh, reframe_mode)
    base = max(0.0, min(it.get("s", clip_start) for it in items) - 1.0)
    span = max(it.get("e", clip_end) for it in items) - base + 1.0
    sin = ["-ss", f"{base:.3f}", "-t", f"{span:.3f}", "-i", str(src)]
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
    emo = EmojiPop(fx.get("emoji") or [], fx.get("emoji_y", 900)) if fx.get("emoji") else None
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
    return {"frames": nfr, "duration": round(nfr / fps, 2), "watermark": wm is not None,
            "reframe": "letterbox" if plan is None else ("center" if len(plan[0]) == 1 else "face")}
