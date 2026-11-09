"""Optional narrator voice with Kokoro-82M (Apache-2.0 weights) via kokoro-onnx (MIT), CPU only.

Model files are downloaded once to ~/.cache/shortrizz/kokoro (or $SHORTRIZZ_KOKORO_DIR). The voice is a
stock Kokoro preset; it never clones the speaker. Clips that use it are marked synthetic_voice in clips.json.
"""
import os
from pathlib import Path
from .util import ensure_verified, log

URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
# SHA-256 of the model-files-v1.0 release assets.
FILES = {
    "kokoro-v1.0.onnx": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}
VOICE = os.environ.get("SHORTRIZZ_TTS_VOICE", "am_michael")
_K = {}
_READY = set()

def _model():
    d = Path(os.environ.get("SHORTRIZZ_KOKORO_DIR", "~/.cache/shortrizz/kokoro")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, digest in FILES.items():
        path = d / name
        if str(path) not in _READY:
            if not ensure_verified(path, URL + name, digest, f"Kokoro {name}"):
                raise FileNotFoundError(f"Kokoro {name} is not available")
            _READY.add(str(path))
        paths.append(path)
    if "k" not in _K:
        from kokoro_onnx import Kokoro
        _K["k"] = Kokoro(str(paths[0]), str(paths[1]))
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
