"""Synthesise Shortrizz's own SFX (pop, whoosh). Generated from code here, dedicated CC0 1.0."""
import wave, numpy as np
from pathlib import Path
SR = 48000
out = Path(__file__).resolve().parent.parent / "shortrizz" / "assets" / "sfx"
out.mkdir(parents=True, exist_ok=True)
def save(name, x):
    x = (np.clip(x / max(1e-9, np.abs(x).max()) * 0.9, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(out / name), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(x.tobytes())
t = np.arange(int(SR * 0.12)) / SR
f = 900 * np.exp(-t * 18) + 300                       # downward chirp "pop"
save("pop.wav", np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 35))
rng = np.random.default_rng(7)
t = np.arange(int(SR * 0.45)) / SR
n = rng.standard_normal(len(t))
env = np.sin(np.pi * t / t[-1]) ** 2
k = np.exp(-((np.fft.rfftfreq(len(t), 1 / SR) - 1800) / 1400) ** 2)  # band-passed noise sweep
save("whoosh.wav", np.fft.irfft(np.fft.rfft(n) * k, len(t)) * env)
(out / "LICENSE.txt").write_text("pop.wav and whoosh.wav are synthesised by scripts/make_sfx.py for Shortrizz and "
                                 "dedicated to the public domain under CC0 1.0.\n")
print("ok")
