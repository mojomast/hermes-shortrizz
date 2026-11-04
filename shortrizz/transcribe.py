import json
from pathlib import Path
from .util import log, models_dir

def transcribe(video, cache_dir, model_size="small", language=None, threads=0):
    """faster-whisper on CPU with word timestamps. Cached as JSON next to the download."""
    cache = Path(cache_dir) / f"{Path(video).stem}.{model_size}.words.json"
    if cache.exists():
        log(f"using cached transcript {cache}")
        return json.loads(cache.read_text())
    from faster_whisper import WhisperModel
    log(f"transcribing with faster-whisper '{model_size}' (CPU int8)…")
    model = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=threads,
                         download_root=str(models_dir() / "whisper"))
    import subprocess, numpy as np
    has_audio = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                                "stream=index", "-of", "csv=p=0", str(video)], capture_output=True, text=True).stdout.strip()
    if not has_audio:
        raise SystemExit(f"{video} has no audio track - nothing to transcribe")
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
    segs, info = model.transcribe(audio, language=language, word_timestamps=True,
                                  vad_filter=True, beam_size=5)
    out = {"language": info.language, "segments": []}
    for s in segs:
        words = [{"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3)}
                 for w in (s.words or []) if w.word.strip()]
        out["segments"].append({"s": round(s.start, 3), "e": round(s.end, 3), "text": s.text.strip(), "words": words})
    cache.write_text(json.dumps(out))
    return out

def all_words(tr):
    return [w for s in tr["segments"] for w in s["words"]]
