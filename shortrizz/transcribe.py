"""faster-whisper transcription with guards against Whisper repetition loops / hallucinations."""
import json, subprocess, zlib
from pathlib import Path
import numpy as np
from .util import log, models_dir

TRANSCRIPT_VERSION = 3
import os
# int8 CPU decoding silently dropped whole minutes of clear speech on some sources (Basis, S79u6BtYvQ0);
# float32 is ~1.6x slower but reliable. Override with SHORTRIZZ_COMPUTE_TYPE=int8.
COMPUTE_TYPE = os.environ.get("SHORTRIZZ_COMPUTE_TYPE", "float32")
SR = 16000

# Settings that stop the classic "I'm I'm I'm" loop: no conditioning on previous (possibly
# hallucinated) text, temperature fallback when a window's text compresses too well or is
# low-probability, and dropping hallucinated words inside long silences.
# No initial_prompt: a filler-style "Umm, uh…" priming prompt made Whisper skip most speech in tests
# (4 vs 159 words on a 60 s chunk), so fillers are caught by rules + the LLM edit pass instead.
WHISPER_KW = dict(word_timestamps=True, vad_filter=True, beam_size=5, condition_on_previous_text=False,
                  compression_ratio_threshold=2.2, log_prob_threshold=-1.0, no_speech_threshold=0.6,
                  temperature=(0.0, 0.2, 0.4, 0.6, 0.8),
                  repetition_penalty=1.05, vad_parameters={"min_silence_duration_ms": 500})

# ------------------------------------------------------------------ quality checks
def text_ratio(text):
    """zlib compression ratio: ~1.5-2 for normal speech, >2.4 for loops like 'I'm I'm I'm'."""
    b = text.encode()
    return len(b) / max(1, len(zlib.compress(b))) if b else 0.0

def is_looping(text):
    """Short-period repetition ("I'm I'm I'm", "the the the", "I'm it. I'm itch. I'm it.").

    Counts tokens equal to the token 1 or 2 places back; normal speech scores ~0-0.1,
    Whisper loops > 0.4. Long repeated sentences are allowed (real speech repeats too)."""
    toks = [t.strip(".,!?;:'\"").lower() for t in text.split()]
    toks = [t for t in toks if t]
    if len(toks) < 4:
        return False
    rep = sum(1 for i in range(1, len(toks)) if toks[i] == toks[i - 1] or (i > 1 and toks[i] == toks[i - 2]))
    frac = rep / (len(toks) - 1)
    return frac > 0.4 or len(set(toks)) <= 2

def quality(tr, duration=None):
    """Return dict with words/min, loop fraction and an ok flag."""
    segs = tr.get("segments", [])
    words = sum(len(s["words"]) for s in segs)
    spoken = sum(max(0.0, s["e"] - s["s"]) for s in segs)
    loop_dur = sum(max(0.0, s["e"] - s["s"]) for s in segs if is_looping(s["text"]))
    dur = duration or (segs[-1]["e"] if segs else 0)
    wpm = words / max(dur / 60, 1e-6)
    all_toks = [w["w"].strip(".,!?").lower() for s in segs for w in s["words"]]
    uniq = len(set(all_toks)) / max(1, len(all_toks))
    loop_frac = loop_dur / max(spoken, 1e-6)
    global_loop = is_looping(" ".join(w["w"] for s in segs for w in s["words"]))
    sparse = dur > 120 and wpm < 40      # a talk has 120-200 wpm; Whisper dropping speech shows up here
    ok = bool(segs) and loop_frac < 0.15 and not global_loop and not sparse
    return {"words": words, "wpm": round(wpm, 1), "loop_frac": round(loop_frac, 3), "unique_ratio": round(uniq, 3),
            "global_loop": global_loop, "sparse": sparse, "ok": ok}

# ------------------------------------------------------------------ decoding
def load_audio(video):
    has_audio = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                                "stream=index", "-of", "csv=p=0", str(video)], capture_output=True, text=True).stdout.strip()
    if not has_audio:
        raise SystemExit(f"{video} has no audio track - nothing to transcribe")
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0

def _run(model, audio, offset=0.0, **kw):
    opts = {**WHISPER_KW, **kw}
    segs, info = model.transcribe(audio, **opts)
    out = []
    for s in segs:
        words = [{"w": w.word.strip(), "s": round(w.start + offset, 3), "e": round(w.end + offset, 3)}
                 for w in (s.words or []) if w.word.strip()]
        if words:
            out.append({"s": round(s.start + offset, 3), "e": round(s.end + offset, 3), "text": s.text.strip(),
                        "words": words})
    return out, info

def _repair_loops(model, audio, segs, language):
    """Re-transcribe each looping segment's span on its own (no context, higher temperature)."""
    fixed, n = [], 0
    for s in segs:
        if not is_looping(s["text"]):
            fixed.append(s); continue
        n += 1
        a0, a1 = max(0, int((s["s"] - 0.5) * SR)), int((s["e"] + 0.5) * SR)
        redo, _ = _run(model, audio[a0:a1], offset=a0 / SR, language=language, temperature=(0.2, 0.4, 0.6),
                       vad_filter=False)
        redo = [r for r in redo if not is_looping(r["text"])]
        fixed.extend(redo)  # drop the span entirely if it still loops (usually music / noise)
    if n:
        log(f"  re-transcribed {n} looping segment(s)")
    fixed.sort(key=lambda s: s["s"])
    return fixed

def speech_coverage(audio, segs):
    """Fraction of VAD-detected speech that the transcript covers. Catches Whisper silently skipping minutes."""
    try:
        from faster_whisper.vad import get_speech_timestamps, VadOptions
        ts = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=500))
    except Exception:  # noqa: BLE001
        return 1.0
    spans = []
    for x, y in sorted((s["s"] - 1.5, s["e"] + 1.5) for s in segs):
        if spans and x <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], y))
        else:
            spans.append((x, y))
    tot = cov = 0.0
    j = 0
    for t in ts:
        a, b = t["start"] / SR, t["end"] / SR
        tot += b - a
        for x, y in spans:
            if y < a:
                continue
            if x > b:
                break
            cov += max(0.0, min(b, y) - max(a, x))
    return cov / tot if tot > 5 else 1.0

def transcribe(video, cache_dir, model_size="small", language=None, threads=0):
    """faster-whisper on CPU with word timestamps. Cached as JSON; bad/old caches are redone."""
    cache = Path(cache_dir) / f"{Path(video).stem}.{model_size}.words.json"
    if cache.exists():
        tr = json.loads(cache.read_text())
        q = quality(tr)
        if q["ok"] and tr.get("version", 0) < 3:  # old caches: check they did not skip speech
            q["coverage"] = round(float(speech_coverage(load_audio(video), tr["segments"])), 3)
            q["ok"] = q["coverage"] >= 0.8
        if q["ok"]:  # older-version caches are kept when they pass the loop checks
            log(f"using cached transcript {cache}")
            return tr
        log(f"cached transcript rejected (version {tr.get('version')}, quality {q}); re-transcribing")
    from faster_whisper import WhisperModel
    log(f"transcribing with faster-whisper '{model_size}' (CPU {COMPUTE_TYPE})…")
    model = WhisperModel(model_size, device="cpu", compute_type=COMPUTE_TYPE, cpu_threads=threads,
                         download_root=str(models_dir() / "whisper"))
    audio = load_audio(video)
    dur = len(audio) / SR
    segs, info = _run(model, audio, language=language)
    lang = language or info.language
    segs = _repair_loops(model, audio, segs, lang)
    tr = {"version": TRANSCRIPT_VERSION, "language": lang, "segments": segs}
    q = quality(tr, dur)
    q["coverage"] = round(float(speech_coverage(audio, segs)), 3)
    q["ok"] = bool(q["ok"] and q["coverage"] >= 0.8)
    if not q["ok"]:
        log(f"  transcript still looks bad {q}; retrying with language={lang}, no VAD, greedy fallback")
        segs, _ = _run(model, audio, language=lang, vad_filter=False, beam_size=1)
        segs = _repair_loops(model, audio, segs, lang)
        tr["segments"] = segs
        q = quality(tr, dur)
        q["coverage"] = round(float(speech_coverage(audio, segs)), 3)
        q["ok"] = bool(q["ok"] and q["coverage"] >= 0.8)
    tr["quality"] = q
    log(f"  transcript: {q['words']} words, {q['wpm']} wpm, loop {q['loop_frac']:.0%}")
    if not q["ok"]:
        why = ("Whisper skipped speech" if q["coverage"] < 0.8 else
               "little or no speech" if q["sparse"] else "Whisper kept looping")
        raise SystemExit(f"transcript failed sanity checks ({why}; {q}). The audio may be music, noise or a "
                         f"non-speech track. Try --model medium or --language en.")
    cache.write_text(json.dumps(tr, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    return tr

def _json_default(o):
    """numpy scalars/arrays (e.g. numpy.bool_ from quality checks) -> plain Python."""
    if hasattr(o, "tolist"):
        return o.tolist()
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")

def all_words(tr):
    """Flat word list; marks Whisper segment edges (ss/se) as fallback phrase boundaries."""
    out = []
    for s in tr["segments"]:
        ws = s["words"]
        for i, w in enumerate(ws):
            out.append({**w, "ss": i == 0, "se": i == len(ws) - 1})
    return out
