"""Optional narrator voice with Kokoro-82M (Apache-2.0 weights) via kokoro-onnx (MIT), CPU only.

Model files are downloaded once to ~/.cache/shortrizz/kokoro (or $SHORTRIZZ_KOKORO_DIR). The voice is a
stock Kokoro preset; it never clones the speaker. Clips that use it are marked synthetic_voice in clips.json.
"""
import os, subprocess
from pathlib import Path
from .util import log

URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
VOICE = os.environ.get("SHORTRIZZ_TTS_VOICE", "am_michael")
_K = {}

def _model():
    d = Path(os.environ.get("SHORTRIZZ_KOKORO_DIR", "~/.cache/shortrizz/kokoro")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        if not (d / f).exists():
            log(f"  downloading Kokoro {f} …")
            subprocess.run(["curl", "-sSLf", "-o", str(d / f), URL + f], check=True)
    if "k" not in _K:
        from kokoro_onnx import Kokoro
        _K["k"] = Kokoro(str(d / FILES[0]), str(d / FILES[1]))
    return _K["k"]

def speak(text, out_wav, speed=1.1):
    """Write text as a WAV; returns its duration in seconds, or None if TTS is unavailable."""
    try:
        import soundfile as sf
        samples, sr = _model().create(text, voice=VOICE, speed=speed, lang="en-us")
        sf.write(str(out_wav), samples, sr)
        return len(samples) / sr
    except Exception as e:  # noqa: BLE001
        log(f"  TTS unavailable ({e}); skipping narrator")
        return None
