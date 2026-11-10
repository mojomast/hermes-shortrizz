"""One 16:9 highlights video from several scored segments of a single source.

The picture is never cropped to vertical. Every segment is fitted into one 16:9 frame
(scaled down, padded if the source is not already 16:9), one frame rate and -14 LUFS,
then joined with a short audio and video crossfade, or a hard cut.
"""
import shutil, subprocess, tempfile
from pathlib import Path
from .util import log, ffprobe_duration
from . import captions, money, render


def fit_16x9(sw, sh, max_width=1920):
    """Even 16:9 output size that fits the whole frame. Never upscales, never crops.

    A source that is already 16:9 keeps its size (capped at `max_width`). Anything else
    is letterboxed or pillarboxed into 16:9.
    """
    sw, sh = int(sw), int(sh)
    if sw < 2 or sh < 2:
        raise ValueError(f"bad frame size {sw}x{sh}")
    max_width = max(160, int(max_width))
    max_width -= max_width % 2
    ratio = sw / float(sh)
    target = 16 / 9
    if abs(ratio - target) <= 0.02 and sw <= max_width and sw % 2 == 0 and sh % 2 == 0:
        return sw, sh
    if abs(ratio - target) <= 0.02:
        w = min(sw, max_width)
        w -= w % 2
        h = int(round(w * sh / float(sw)))
        h -= h % 2
        return max(2, w), max(2, h)
    long_side = max(sw, int(round(sh * target)))
    w = min(max_width, long_side)
    w -= w % 2
    h = int(round(w / target))
    h -= h % 2
    h = max(2, h)
    if min(w / float(sw), h / float(sh)) > 1.001:
        if ratio >= target:
            w = max(2, sw - sw % 2)
            h = max(2, int(round(w / target)))
        else:
            h = max(2, sh - sh % 2)
            w = max(2, int(round(h * target)))
        w -= w % 2
        h -= h % 2
    return max(2, w), max(2, h)


def norm_fps(fps):
    """One constant frame rate for every segment. Sources above 30 fps render at 30."""
    fps = float(fps or 30)
    if fps > 30.5:
        return 30.0
    for n in (30.0, 25.0, 24.0, 29.97, 23.976):
        if abs(fps - n) < 0.02:
            return n
    return max(15.0, round(min(fps, 30.0), 3))


def transition_offsets(durations, fade):
    """Seconds into the running cut where each crossfade starts (one per join)."""
    offs, acc = [], 0.0
    for i, d in enumerate(durations[:-1]):
        acc += d
        offs.append(round(acc - (i + 1) * fade, 3))
    return offs


def joined_duration(durations, fade):
    if not durations:
        return 0.0
    return float(sum(durations) - max(0, len(durations) - 1) * max(0.0, fade))


def local_words(words, a, b, origin):
    """Word times shifted so the segment's media start is t=0."""
    out = []
    for w in words[a:b + 1]:
        e = w["e"] - origin
        if e <= 0.02:
            continue
        out.append({**w, "s": round(max(0.0, w["s"] - origin), 3), "e": round(max(0.05, e), 3)})
    return out


def caption_window(words, dur, fade, fade_in, fade_out):
    """Leave the crossfade edges clear so two caption lines don't dissolve on top of each other."""
    lo = fade if fade_in else 0.0
    hi = (dur - fade) if fade_out else dur
    if hi - lo < 0.25:
        lo, hi = 0.0, dur
    kept = []
    for w in words:
        if w["e"] <= lo + 0.02 or w["s"] >= hi - 0.02:
            continue
        nw = {**w, "s": max(w["s"], lo), "e": min(w["e"], hi)}
        if nw["e"] - nw["s"] >= 0.04:
            kept.append(nw)
    return kept


def _clock(t):
    whole = int(round(max(0.0, float(t))))
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def chapters_text(segments):
    """Timestamp list: output time, title, virality score, and where it sat in the source."""
    lines, used = [], set()
    for seg in segments:
        stamp = int(round(float(seg.get("out_start") or 0)))
        while stamp in used:
            stamp += 1
        used.add(stamp)
        title = " ".join(str(seg.get("title") or "Highlight").split())
        lines.append(f"{_clock(stamp)} {title} — score {int(seg.get('virality_score', 0))}"
                     f" — source {_clock(seg['start'])}–{_clock(seg['end'])}")
    return ("\n".join(lines) + "\n") if lines else ""


def _esc(path):
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _ffmpeg(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode != 0:
        err = (r.stderr or "").strip().splitlines()
        raise RuntimeError(err[-1] if err else f"ffmpeg exit {r.returncode}")


def _has_audio(src):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                        "-show_entries", "stream=index", "-of", "csv=p=0", str(src)],
                       capture_output=True, text=True)
    return bool((r.stdout or "").strip())


def _encode_segment(src, start, dur, path, w, h, fps, ass_path, loudnorm, preset, crf, hero=None):
    """``hero`` is ``{"png", "hold"}``. The still covers the first ``hold`` seconds. Audio is the source span."""
    base_vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
               f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,fps={fps:g},setsar=1,format=yuv420p")
    hold = float(hero["hold"]) if hero and hero.get("png") else 0.0
    use_hero = hold > 0.01 and Path(hero["png"]).is_file()
    base = "aformat=sample_rates=48000:channel_layouts=stereo"

    def video_tail(body):
        if ass_path:
            return body + f",ass='{_esc(ass_path)}'"
        return body

    def cmd(af):
        head = ["ffmpeg", "-hide_banner", "-v", "error", "-y",
                "-ss", f"{max(0.0, start):.3f}", "-t", f"{dur:.3f}", "-i", str(src)]
        if not use_hero:
            return head + ["-map", "0:v:0", "-map", "0:a:0", "-vf", video_tail(base_vf), "-af", af,
                           "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                           "-profile:v", "high", "-pix_fmt", "yuv420p",
                           "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
                           "-movflags", "+faststart", str(path)]
        # The still is looped for the whole segment and shown only while t < hold, so the
        # output clock stays the segment length. eof_action=pass keeps the real picture after it.
        head += ["-loop", "1", "-framerate", f"{fps:g}", "-t", f"{dur:.3f}", "-i", str(hero["png"])]
        shown = video_tail(
            f"[base][hero]overlay=eof_action=pass:shortest=1:enable='lt(t\\,{hold:.3f})'")
        fc = (f"[0:v]{base_vf}[base];[1:v]scale={w}:{h}:flags=lanczos,setsar=1,format=rgba[hero];{shown}[vout]")
        return head + ["-filter_complex", fc, "-map", "[vout]", "-map", "0:a:0", "-af", af,
                       "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                       "-profile:v", "high", "-pix_fmt", "yuv420p",
                       "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
                       "-movflags", "+faststart", str(path)]

    if loudnorm:
        try:
            _ffmpeg(cmd(base + ",loudnorm=I=-14:TP=-1.5:LRA=11"))
            return
        except RuntimeError as e:
            log(f"  loudnorm skipped on a segment ({e})")
            path.unlink(missing_ok=True)
    _ffmpeg(cmd(base))


def _overlay_xy(position, margin):
    if position == "top-left":
        return str(margin), str(margin)
    if position == "bottom-right":
        return f"main_w-overlay_w-{margin}", f"main_h-overlay_h-{margin}"
    if position == "bottom-left":
        return str(margin), f"main_h-overlay_h-{margin}"
    return f"main_w-overlay_w-{margin}", str(margin)


def _join(parts, durs, fade, out_path, preset, crf, wm):
    """Concat or crossfade `parts` into out_path. `wm` is an optional png overlay."""
    n = len(parts)
    inputs = []
    for p in parts:
        inputs += ["-i", str(p)]
    wm_index = None
    if wm and wm.get("png") and Path(wm["png"]).is_file():
        inputs += ["-i", str(wm["png"])]
        wm_index = n
    chains, v, a = [], "[0:v]", "[0:a]"
    if n == 1:
        pass
    elif fade <= 0:
        body = "".join(f"[{i}:v][{i}:a]" for i in range(n))
        chains.append(f"{body}concat=n={n}:v=1:a=1[vcat][acat]")
        v, a = "[vcat]", "[acat]"
    else:
        for i, off in enumerate(transition_offsets(durs, fade), 1):
            last = i == n - 1
            vout = "[vxf]" if last else f"[v{i}]"
            aout = "[axf]" if last else f"[a{i}]"
            chains.append(f"{v}[{i}:v]xfade=transition=fade:duration={fade:.3f}:offset={off:.3f}{vout}")
            chains.append(f"{a}[{i}:a]acrossfade=d={fade:.3f}:c1=qsin:c2=qsin{aout}")
            v, a = vout, aout
    if wm_index is not None:
        ww = max(32, int(wm["width"]))
        margin = max(12, int(wm["frame_w"] * 0.03))
        x, y = _overlay_xy(wm.get("position") or "top-right", margin)
        chains.append(f"[{wm_index}:v]scale={ww}:-1,format=rgba[wm]")
        chains.append(f"{v}[wm]overlay={x}:{y}:format=auto[vout]")
        v = "[vout]"
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", *inputs]
    if not chains:
        cmd += ["-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-movflags", "+faststart", str(out_path)]
    else:
        cmd += ["-filter_complex", ";".join(chains), "-map", v, "-map", a,
                "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                "-profile:v", "high", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                "-movflags", "+faststart", str(out_path)]
    _ffmpeg(cmd)


def _usable_fade(durations, fade):
    fade = float(fade or 0)
    if len(durations) < 2 or fade <= 0:
        return 0.0
    fade = min(fade, *(d / 3 for d in durations))
    return 0.0 if fade < 0.05 else round(fade, 3)


def _timeline(durations, fade):
    t, out = 0.0, []
    for i, d in enumerate(durations):
        out.append({"duration": round(d, 3), "out_start": round(t, 3), "out_end": round(t + d, 3)})
        t += d - (fade if i < len(durations) - 1 else 0)
    return out, round(t, 3)


def render_highlights(src, segments, out_path, words=None, style=None, crossfade=0.4, loudnorm=True,
                      preset="medium", crf=20, max_width=1920, watermark=None, keep_ass=False, progress=None,
                      money_shot=None):
    """Render `segments` (in play order) to one 16:9 MP4. Returns geometry, duration and per-segment times.

    `style` is a caption style dict; None leaves captions off. `watermark` is a settings() dict.
    """
    src, out_path = Path(src), Path(out_path)
    if not segments:
        raise ValueError("no segments to render")
    if not _has_audio(src):
        raise RuntimeError(f"no audio stream in {src}")
    sw, sh, src_fps = render.probe(src)
    w, h = fit_16x9(sw, sh, max_width)
    fps = norm_fps(src_fps)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial = out_path.with_suffix(".partial" + out_path.suffix)
    wm = None
    if watermark and watermark.get("watermark"):
        from .watermark import ASSETS
        png = ASSETS / ("watermark-madewith.png" if watermark.get("text") else "watermark.png")
        wm = {"png": png, "width": int(w * float(watermark.get("width") or 0.26)),
              "position": watermark.get("position") or "top-right", "frame_w": w, "frame_h": h}
    planned = [max(0.3, float(s["end"]) - float(s["start"])) for s in segments]
    fade0 = _usable_fade(planned, crossfade)
    work = Path(tempfile.mkdtemp(prefix="shortrizz-hl-"))
    show_money = money.enabled(money_shot)
    hero_plan = money.describe(None, on=False)
    hero_png = None
    loaded_styles = {}

    def raw_style(seg):
        name = seg.get("caption_style")
        if not name:
            return style
        if name not in loaded_styles:
            loaded_styles[name] = captions.load_style(name)
        return loaded_styles[name]

    try:
        if show_money:
            ranges = money.ranges_from_segments(segments)
            hero_plan_full = money.choose(src, ranges, sw, sh, fps)
            hero_plan = money.describe(hero_plan_full, on=True)
            if hero_plan_full.get("hold_frames") and hero_plan_full.get("t") is not None:
                still = money.fitted_still(src, hero_plan_full["t"], sw, sh, w, h)
                if still is not None:
                    hero_png = work / "hero.png"
                    money.write_png(still, hero_png)
                else:
                    hero_plan = {**hero_plan, "reason": "no-frame", "hold_sec": 0.0}
        parts = []
        for i, seg in enumerate(segments):
            if progress:
                progress("render", i, len(segments))
            dur = planned[i]
            use_style = raw_style(seg)
            ass = None
            if use_style is not None and words is not None and seg.get("word_range"):
                a, b = seg["word_range"]
                shown = caption_window(local_words(words, a, b, float(seg["start"])), dur, fade0,
                                        fade_in=i > 0 and fade0 > 0, fade_out=i < len(segments) - 1 and fade0 > 0)
                if shown:
                    ass = work / f"seg-{i:02d}.ass"
                    look = captions.for_landscape(use_style, w, h)
                    ass.write_text(captions.build_ass(
                        shown, 0.0, dur, look, W=w, H=h, money_shot=show_money and i == 0))
                    if keep_ass:
                        shutil.copy(ass, out_path.parent / ass.name)
            part = work / f"seg-{i:02d}.mp4"
            log(f"  segment {i + 1}/{len(segments)}  {seg['start']:.1f}-{seg['end']:.1f}s -> {w}x{h}@{fps:g}")
            hero = {"png": hero_png, "hold": hero_plan.get("hold_sec") or 0} if i == 0 and hero_png else None
            _encode_segment(src, float(seg["start"]), dur, part, w, h, fps, ass, loudnorm, preset, crf, hero=hero)
            parts.append(part)
        durs = [ffprobe_duration(p) for p in parts]
        fade_used, last_err = fade0, None
        for shrink in (0.0, 0.05, 0.10):
            fade_try = _usable_fade(durs, max(0.0, fade0 - shrink))
            partial.unlink(missing_ok=True)
            try:
                if len(parts) == 1 and not wm:
                    shutil.copyfile(parts[0], partial)
                    fade_used = 0.0
                else:
                    _join(parts, durs, fade_try, partial, preset, crf, wm)
                    fade_used = fade_try
                break
            except RuntimeError as e:
                last_err = e
                log(f"  join failed ({e}); retrying with a shorter crossfade")
        else:
            raise RuntimeError(f"could not join highlight segments: {last_err}")
        lufs = render.check_audio(partial)
        partial.replace(out_path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    timeline, planned_total = _timeline(durs, fade_used)
    duration = round(ffprobe_duration(out_path), 3)
    log(f"  highlights {w}x{h} {duration:.1f}s  fade {fade_used:.2f}s  {lufs:.1f} LUFS")
    return {"width": w, "height": h, "fps": fps, "duration": duration, "planned_duration": planned_total,
            "crossfade": fade_used, "audio_lufs": lufs, "watermark": bool(wm), "segments": timeline,
            "money_shot": hero_plan}
