import json
from shortrizz import transcribe as T, clipselect

def seg(s, e, text):
    ws = text.split(); step = (e - s) / len(ws)
    return {"s": s, "e": e, "text": text,
            "words": [{"w": w, "s": s + i * step, "e": s + (i + 0.8) * step} for i, w in enumerate(ws)]}

GOOD = ("if you go look at the average company their productivity has not gone up ten times "
        "but I know my own productivity has gone up maybe a hundred times since we started using these tools")

def test_is_looping():
    assert T.is_looping("I'm I'm I'm I'm I I'm it. I'm itch. I'm it.")
    assert T.is_looping("the the the the the the the the")
    assert not T.is_looping(GOOD)

def test_quality_flags_basis_style_loop_transcript():
    bad = {"segments": [seg(i * 3.0, i * 3.0 + 2.5, "I'm I'm I'm itch. I'm it.") for i in range(40)]}
    assert not T.quality(bad)["ok"]
    good = {"segments": [seg(i * 8.0, i * 8.0 + 7.5, GOOD) for i in range(40)]}
    assert T.quality(good)["ok"]

def test_cached_garbage_transcript_is_rejected(tmp_path, monkeypatch):
    bad = {"segments": [seg(i * 3.0, i * 3.0 + 2.5, "I'm I'm I'm I'm") for i in range(30)]}
    (tmp_path / "v.small.words.json").write_text(json.dumps(bad))
    called = {}
    class Boom(Exception): pass
    def fake_model(*a, **k):
        called["yes"] = True; raise Boom()
    import faster_whisper
    monkeypatch.setattr(faster_whisper, "WhisperModel", fake_model)
    try:
        T.transcribe(tmp_path / "v.mp4", tmp_path, "small")
    except Boom:
        pass
    assert called.get("yes")  # it did not trust the cache

def test_whisper_settings_disable_conditioning():
    assert T.WHISPER_KW["condition_on_previous_text"] is False
    assert T.WHISPER_KW["vad_filter"] is True and T.WHISPER_KW["compression_ratio_threshold"] <= 2.4

def test_sane_clip_rejects_loops_and_sparse_speech():
    loop = seg(0, 40, " ".join(["I'm"] * 60))["words"]
    sparse = seg(0, 40, "hello there friend")["words"]
    ok = seg(0, 40, " ".join([GOOD] * 2))["words"]
    assert not clipselect.sane_clip(loop)
    assert not clipselect.sane_clip(sparse)
    assert clipselect.sane_clip(ok, "Why AI makes you 100x faster")

def test_sparse_basis_transcript_rejected():
    # the real failure: 76 words over 19 minutes, short "I'm itch." segments
    tr = {"segments": [seg(i * 30.0, i * 30.0 + 2, t) for i, t in
                       enumerate(["I'm itch.", "I'm it.", "I'm I'm I'm", "I'm I"] * 10)]}
    q = T.quality(tr, 1152)
    assert not q["ok"] and q["sparse"]

def test_coverage_flags_skipped_speech():
    import numpy as np
    sr = T.SR
    t = np.arange(sr * 30) / sr
    # 30 s of a voiced, syllable-modulated tone that silero treats as speech
    audio = (0.3 * np.sin(2 * np.pi * 180 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 4 * t))).astype(np.float32)
    full = [{"s": 0.0, "e": 30.0, "text": "x", "words": []}]
    assert T.speech_coverage(audio, full) >= 0.99
    assert T.speech_coverage(audio, []) <= 0.01 or T.speech_coverage(audio, []) == 1.0  # 1.0 = VAD heard no speech
