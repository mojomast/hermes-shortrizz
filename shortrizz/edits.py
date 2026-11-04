"""Deeper edits: dead-air tightening, LLM word-index EDL, hook text, punch-ins, cold open, loops, TTS.

The LLM only ever proposes *word indices*; this module validates them and does all the timing work
(gap compression, padding, VAD-aligned cut points, crossfades). The EDL is saved next to each clip
(NAME.edl.json) and in the cache so `restyle` re-renders without another LLM call.
"""
import hashlib, json, os, re
from pathlib import Path
from .util import log

EDL_VERSION = 1

# platform safe zones for 1080x1920 (union of TikTok / Reels / Shorts UI)
SAFE = {"top": 288, "bottom_y": 1240, "left": 120, "right": 192}

PRESETS = {  # --edit
    "off":        None,
    "light":      {"max_gap": 0.60, "target_gap": 0.25, "pad_pre": 0.08, "pad_post": 0.12, "xfade_ms": 15},
    "normal":     {"max_gap": 0.35, "target_gap": 0.15, "pad_pre": 0.08, "pad_post": 0.12, "xfade_ms": 15},
    "aggressive": {"max_gap": 0.25, "target_gap": 0.08, "pad_pre": 0.06, "pad_post": 0.10, "xfade_ms": 12},
}
MIN_KEEP, MIN_REMOVE = 0.25, 0.15
MAX_DROP_FRAC = 0.12          # never let edits remove more than 12% of a clip's words
FILLERS = {"um", "uh", "erm", "er", "uhm", "umm", "uhh", "hmm", "mm", "ah"}
PROTECTED = re.compile(r"(\d|^(not|never|no|none|nobody|nothing|nor|neither|cannot|without)$|n't$)", re.I)
# faster-whisper normalises fillers away; a verbatim-style prompt makes it keep most of them
FILLER_PROMPT = "Umm, let me think like, hmm... Okay, here's what I'm, uh, thinking. So, um, yeah."

OPT_INS = ("cold_open", "tts_intro", "tts_outro", "zoom", "loop", "emoji", "sfx")
DEFAULTS = {"edit": "normal", "llm_edits": True, "hook_text": True, "loudnorm": True,
            **{k: False for k in OPT_INS}}

def norm(w):
    return re.sub(r"[^\w']", "", w.lower())

def options(cli=None):
    """CLI value > SHORTRIZZ_<KEY> env > config file key > default (all opt-ins off)."""
    from .watermark import _user_config
    cfg = _user_config()
    out = dict(DEFAULTS)
    for k in DEFAULTS:
        if k in cfg:
            out[k] = cfg[k]
        ev = os.environ.get(f"SHORTRIZZ_{k.upper()}", "")
        if ev:
            out[k] = ev if k == "edit" else ev.lower() in ("1", "true", "yes", "on")
    for k, v in (cli or {}).items():
        if v is not None:
            out[k] = v
    if out["edit"] not in PRESETS:
        raise SystemExit(f"--edit must be one of {', '.join(PRESETS)}")
    return out

# ------------------------------------------------------------------ rule-based drops
def rule_drops(words, a, b):
    """um/uh with a little air around them, and exact 1-3 word repeats within 1.5 s ("I I think")."""
    drops = []
    for i in range(a, b + 1):
        if norm(words[i]["w"]) in FILLERS:
            gb = words[i]["s"] - words[i - 1]["e"] if i > a else 1
            ga = words[i + 1]["s"] - words[i]["e"] if i < b else 1
            if gb >= 0.06 or ga >= 0.06:
                drops.append({"w0": i, "w1": i, "reason": "filler"})
    i = a
    while i <= b:
        for n in (3, 2, 1):
            j = i + n
            if j + n - 1 > b:
                continue
            x = [norm(words[k]["w"]) for k in range(i, j)]
            y = [norm(words[k]["w"]) for k in range(j, j + n)]
            if x == y and all(x) and words[j + n - 1]["e"] - words[i]["s"] < 1.5 and not any(PROTECTED.search(t) for t in x):
                drops.append({"w0": i, "w1": j - 1, "reason": "repeat"}); i = j - 1
                break
        i += 1
    return drops

# ------------------------------------------------------------------ LLM pass
EDL_PROMPT = """You are a short-form video editor. Below is the transcript of ONE vertical clip, one word per
line as [index] word (start s). Plan tight, natural edits. Rules:
- "drop": ONLY false starts, abandoned phrases, stutters/repeats and filler (um, uh, "you know" used as filler).
  Use index ranges w0..w1 inclusive. Never drop numbers, negations (not/never/no/n't) or anything that changes meaning.
  Be conservative: at most {maxdrop} words total. If the speech is clean, return [].
- "hook_text": 2-6 word on-screen hook for the first 3 seconds that is TRUE to the clip (no clickbait), Title Case.
- "emphasis": up to {nemph} indices of the most important single words (for a punch-in zoom), spread out.
- "emoji": up to {nemoji} {{"w": index, "emoji": one emoji}} on concrete nouns/emotions, spread out.
- "cold_open": the single most striking 4-12 consecutive words (a complete phrase) to flash first, {{"w0","w1"}}.
- "intro": one spoken narrator line, max 12 words, giving context (third person, no hype, don't impersonate the speaker).
- "cta": short end call to action, max 5 words (e.g. "Follow for part 2").
- "loop_end": index of the last word to keep so the ending flows back into the opening line, or null.
Return ONLY JSON: {{"drop":[{{"w0":i,"w1":j,"reason":"false_start|repeat|filler"}}],"hook_text":"...","emphasis":[i],
"emoji":[{{"w":i,"emoji":"🔥"}}],"cold_open":{{"w0":i,"w1":j}},"intro":"...","cta":"...","loop_end":i}}

Clip title: {title}
Words:
{lines}
"""

def llm_plan(words, a, b, title):
    from .clipselect import llm_chat, parse_json
    dur = words[b]["e"] - words[a]["s"]
    lines = "\n".join(f"[{i}] {words[i]['w']} ({words[i]['s'] - words[a]['s']:.1f})" for i in range(a, b + 1))
    p = EDL_PROMPT.format(maxdrop=int((b - a + 1) * MAX_DROP_FRAC), nemph=max(2, int(dur / 6)),
                          nemoji=max(1, int(dur / 10)), title=title, lines=lines)
    msgs = [{"role": "user", "content": p}]
    for extra in ({"reasoning_effort": "low"}, {"thinking": {"type": "disabled"}}):
        try:
            return parse_json(llm_chat(msgs, max_tokens=6000, extra=extra))
        except Exception as e:  # noqa: BLE001 - retry once, then fall back to rules only
            log(f"  edit LLM failed ({e})")
    return {}

def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None

def validate(plan, words, a, b, rule=None):
    """Clamp an LLM plan to safe, word-boundary edits. Returns a clean dict."""
    nwords = b - a + 1
    cap = int(nwords * MAX_DROP_FRAC)
    drops, removed, taken = [], 0, set()
    for d in (rule or []) + list(plan.get("drop") or []):
        w0, w1 = _int(d.get("w0")), _int(d.get("w1", d.get("w0")))
        if w0 is None or w1 is None or w0 > w1 or w0 <= a and w1 >= b:
            continue
        w0, w1 = max(a, w0), min(b, w1)
        rng = set(range(w0, w1 + 1))
        if rng & taken or w1 - w0 + 1 > 6:
            continue
        if any(PROTECTED.search(norm(words[k]["w"])) for k in rng):
            continue
        if removed + len(rng) > cap:
            continue
        drops.append({"w0": w0, "w1": w1, "reason": str(d.get("reason", "llm"))[:20],
                      "text": " ".join(words[k]["w"] for k in range(w0, w1 + 1))})
        taken |= rng; removed += len(rng)
    drops.sort(key=lambda d: d["w0"])
    hook = re.sub(r"\s+", " ", str(plan.get("hook_text") or "")).strip().strip('"')
    hook = " ".join(hook.split()[:6]) if len(hook.split()) >= 2 else ""
    emph, last = [], -9
    for w in plan.get("emphasis") or []:
        w = _int(w)
        if w is not None and a <= w <= b and w not in taken and words[w]["s"] - last >= 3.0:
            emph.append(w); last = words[w]["s"]
    emoji, last = [], -9
    for e in plan.get("emoji") or []:
        w = _int((e or {}).get("w")) if isinstance(e, dict) else None
        em = str(e.get("emoji", "")).strip() if isinstance(e, dict) else ""
        if w is not None and a <= w <= b and w not in taken and 0 < len(em) <= 4 and words[w]["s"] - last >= 4.0:
            emoji.append({"w": w, "emoji": em}); last = words[w]["s"]
    co = plan.get("cold_open") or {}
    c0, c1 = _int(co.get("w0")), _int(co.get("w1"))
    cold = None
    if c0 is not None and c1 is not None and a + 3 <= c0 <= c1 <= b and not (set(range(c0, c1 + 1)) & taken):
        if 1.0 <= words[c1]["e"] - words[c0]["s"] <= 3.5:
            cold = {"w0": c0, "w1": c1, "text": " ".join(words[k]["w"] for k in range(c0, c1 + 1))}
    le = _int(plan.get("loop_end"))
    loop_end = le if le is not None and b - max(3, nwords // 4) <= le <= b and le not in taken else None
    def short(s, n):
        s = re.sub(r"\s+", " ", str(s or "")).strip().strip('"')
        return " ".join(s.split()[:n])
    return {"drop": drops, "hook_text": hook, "emphasis": emph, "emoji": emoji[:6], "cold_open": cold,
            "intro": short(plan.get("intro"), 12), "cta": short(plan.get("cta"), 5) or "Follow for more",
            "loop_end": loop_end}

# ------------------------------------------------------------------ timeline
_VAD = {}
def speech_mask(src, start, end):
    """silero-vad (bundled with faster-whisper) speech regions inside [start,end], in source seconds."""
    key = (str(src), round(start, 2), round(end, 2))
    if key in _VAD:
        return _VAD[key]
    try:
        import subprocess, numpy as np
        from faster_whisper.vad import get_speech_timestamps, VadOptions
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start}", "-t", f"{end - start}", "-i", str(src),
                              "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True).stdout
        audio = np.frombuffer(raw, np.float32)
        ts = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=60, speech_pad_ms=0, threshold=0.5))
        out = [(start + t["start"] / 16000, start + t["end"] / 16000) for t in ts]
    except Exception as e:  # noqa: BLE001
        log(f"  VAD unavailable ({e}); using word timestamps only")
        out = None
    _VAD[key] = out
    return out

def _in_speech(mask, t):
    return mask is not None and any(s <= t <= e for s, e in mask)

def _trail(mask, t, limit):
    """How far speech energy continues after t (trailing consonants), capped at limit."""
    if mask:
        for s, e in mask:
            if s <= t + 0.02 <= e:
                return min(limit, max(0.0, e - t))
    return 0.0

def _lead(mask, t, limit):
    if mask:
        for s, e in mask:
            if s <= t - 0.02 <= e:
                return min(limit, max(0.0, t - s))
    return 0.0

def build_segments(words, a, b, drops, policy, mask=None, loop_end=None):
    """Return source-time segments [(s, e)] covering kept words, tightened by policy."""
    end_idx = loop_end if loop_end is not None else b
    dropped = {k for d in drops for k in range(d["w0"], d["w1"] + 1)}
    kept = [i for i in range(a, end_idx + 1) if i not in dropped]
    if not kept:
        return [(words[a]["s"], words[b]["e"])], []
    if policy is None:
        return [(max(0.0, words[a]["s"] - 0.08), words[end_idx]["e"] + (0.04 if loop_end else 0.12))], []
    T, pre, post = policy["target_gap"], policy["pad_pre"], policy["pad_post"]
    segs, cuts = [], []
    s0 = max(0.0, words[kept[0]]["s"] - pre)
    for x, y in zip(kept, kept[1:]):
        we, ns = words[x]["e"], words[y]["s"]
        gap = ns - we
        skipped = y != x + 1
        if gap > policy["max_gap"] or (skipped and gap > 0.0):
            # keep ~target_gap of air: 60% after the word, 40% before the next, extended by VAD tails
            keep_post = max(T * 0.6, _trail(mask, we, post))
            keep_pre = max(T * 0.4, _lead(mask, ns, pre))
            if keep_post + keep_pre >= gap - MIN_REMOVE and not skipped:
                continue
            e = we + min(keep_post, gap / 2)
            s = ns - min(keep_pre, gap / 2)
            segs.append((s0, e)); cuts.append({"at": round(e, 2), "removed": round(s - e, 2),
                                               "reason": "drop" if skipped else "gap"})
            s0 = s
    tail = 0.04 if loop_end is not None else max(post, _trail(mask, words[kept[-1]]["e"], post))
    segs.append((s0, words[kept[-1]]["e"] + tail))
    merged = []
    for s, e in segs:  # absorb tiny keeps into the previous segment
        if merged and e - s < MIN_KEEP:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged, cuts

def remap(words, idx, segs, offset=0.0):
    """Map source word times onto the output timeline; words outside every segment are dropped."""
    out, base = [], offset
    bounds = []
    for s, e in segs:
        bounds.append((s, e, base)); base += e - s
    for i in idx:
        w = words[i]
        for s, e, b0 in bounds:
            if s - 0.01 <= w["s"] <= e:
                out.append({**w, "i": i, "s": b0 + w["s"] - s, "e": b0 + min(w["e"], e) - s})
                break
    return out, base

def edl_key(src, clip, opts, tr_hash=""):
    h = hashlib.sha1(json.dumps([Path(src).name, round(clip["start"], 2), round(clip["end"], 2), opts.get("edit"),
                                 opts.get("llm_edits"), tr_hash]).encode()).hexdigest()[:10]
    return h

def make_edl(src, words, clip, opts, cache=None, tr_hash="", existing=None):
    a, b = clip["word_range"]
    if existing and existing.get("edl_version") == EDL_VERSION and existing.get("word_range") == [a, b]:
        log("  EDL: reusing saved plan (no LLM)")
        return existing
    cf = Path(cache) / f"{Path(src).stem}.edl.{edl_key(src, clip, opts, tr_hash)}.json" if cache else None
    if cf and cf.exists():
        log("  EDL: cached")
        return json.loads(cf.read_text())
    rule = rule_drops(words, a, b) if opts["edit"] != "off" else []
    plan = llm_plan(words, a, b, clip.get("title", "")) if opts["llm_edits"] and opts["edit"] != "off" else {}
    v = validate(plan, words, a, b, rule)
    if not v["hook_text"]:
        v["hook_text"] = " ".join(re.sub(r"[^\w' $%]", "", clip.get("title", "")).split()[:6])
    edl = {"edl_version": EDL_VERSION, "source": {"start": clip["start"], "end": clip["end"]},
           "word_range": [a, b], "gap_policy": PRESETS.get(opts["edit"]), "llm": bool(plan), **v,
           "audio": {"loudnorm": -14, "true_peak": -1.0}}
    if cf:
        cf.write_text(json.dumps(edl, ensure_ascii=False, indent=1))
    return edl

# ------------------------------------------------------------------ assemble one clip
def assemble(src, words, clip, edl, opts, style, workdir, name):
    """Turn an EDL + options into renderer items, output-time captions/overlays and fx. Pure timing work."""
    from . import captions
    a, b = clip["word_range"]
    policy = edl.get("gap_policy")
    mask = speech_mask(src, clip["start"] - 0.5, clip["end"] + 0.5) if policy else None
    loop_end = edl.get("loop_end") if opts["loop"] else None
    segs, cuts = build_segments(words, a, b, edl["drop"] if policy else [], policy, mask, loop_end)
    items, cap_words, t = [], [], 0.0
    fx = {"xfade_ms": (policy or {}).get("xfade_ms", 15), "bases": [], "punches": [], "drift": [], "sfx": [],
          "emoji": [], "loop": bool(loop_end is not None)}
    sfxdir = Path(__file__).parent / "assets" / "sfx"
    voice = False
    first_t = words[a]["s"]
    if opts["tts_intro"] and edl.get("intro"):
        from . import tts
        wav = Path(workdir) / f"{name}.intro.wav"
        d = tts.speak(edl["intro"], wav)
        if d:
            items.append({"kind": "hold", "dur": d + 0.2, "wav": str(wav), "at": first_t})
            fx["drift"].append((0.0, d + 0.2)); t += d + 0.2; voice = True
            cap_words += _tts_words(edl["intro"], 0.0, d)
    if opts["cold_open"] and edl.get("cold_open"):
        c0, c1 = edl["cold_open"]["w0"], edl["cold_open"]["w1"]
        cs = (max(0.0, words[c0]["s"] - 0.06), words[c1]["e"] + 0.10)
        items.append({"kind": "src", "s": cs[0], "e": cs[1]})
        w, t = remap(words, range(c0, c1 + 1), [cs], t)
        cap_words += w
        fx["bases"].append((t - (cs[1] - cs[0]), t, 1.10 if opts["zoom"] else 1.0))
        if opts["sfx"]:
            fx["sfx"].append((max(0.0, t - 0.25), str(sfxdir / "whoosh.wav")))
    main0 = t
    dropped = {k for d in (edl["drop"] if policy else []) for k in range(d["w0"], d["w1"] + 1)}
    w, t = remap(words, [i for i in range(a, b + 1) if i not in dropped], segs, t)
    cap_words += w
    tt = main0
    for k, (s, e) in enumerate(segs):
        items.append({"kind": "src", "s": s, "e": e})
        if opts["zoom"]:
            fx["bases"].append((tt, tt + e - s, 1.08 if k % 2 else 1.0))
        tt += e - s
    pos = {x["i"]: x for x in w}
    last_sfx = -9
    if opts["zoom"]:
        for i in edl.get("emphasis") or []:
            if i in pos:
                fx["punches"].append((pos[i]["s"], pos[i]["e"] + 0.5))
                if opts["sfx"] and pos[i]["s"] - last_sfx >= 3.5:
                    fx["sfx"].append((pos[i]["s"], str(sfxdir / "pop.wav"))); last_sfx = pos[i]["s"]
    cap_y = min(int(1920 * style["pos_y"]), int(captions.SAFE_BOTTOM_Y - style["size"] * 0.6))
    if opts["emoji"]:
        for e in edl.get("emoji") or []:
            if e["w"] in pos:
                fx["emoji"].append((pos[e["w"]]["s"], e["emoji"]))
                if opts["sfx"] and pos[e["w"]]["s"] - last_sfx >= 3.5:
                    fx["sfx"].append((pos[e["w"]]["s"], str(sfxdir / "pop.wav"))); last_sfx = pos[e["w"]]["s"]
        fx["emoji_y"] = cap_y - style["size"] - 120
    overlays = []
    if opts["hook_text"] and edl.get("hook_text"):
        overlays.append({"text": edl["hook_text"], "t0": 0.0, "t1": min(2.8, t), "pos": "top"})
    if opts["tts_outro"] and edl.get("cta") and loop_end is None:
        from . import tts
        wav = Path(workdir) / f"{name}.outro.wav"
        d = tts.speak(edl["cta"], wav)
        if d:
            last_s = segs[-1][1] - 0.05
            items.append({"kind": "hold", "dur": d + 0.4, "wav": str(wav), "at": last_s})
            fx["drift"].append((t, t + d + 0.4))
            overlays.append({"text": edl["cta"], "t0": t, "t1": t + d + 0.4, "pos": "middle"})
            t += d + 0.4; voice = True
    elif (opts["loop"] or opts["tts_outro"]) and edl.get("cta"):
        overlays.append({"text": edl["cta"], "t0": max(0.0, t - 1.5), "t1": t, "pos": "middle"})
    main_src = sum(e - s for s, e in segs)
    report = {"source_sec": round(clip["end"] - clip["start"], 2), "kept_sec": round(main_src, 2),
              "removed_sec": round(clip["end"] - clip["start"] - main_src, 2), "cuts": len(cuts),
              "gap_cuts": sum(c["reason"] == "gap" for c in cuts), "drop_cuts": sum(c["reason"] == "drop" for c in cuts),
              "dropped_words": [d["text"] for d in edl["drop"]] if policy else [], "final_sec": round(t, 2),
              "hook_text": edl.get("hook_text") if opts["hook_text"] else None,
              "cold_open": (edl.get("cold_open") or {}).get("text") if opts["cold_open"] else None,
              "intro_tts": edl.get("intro") if voice and opts["tts_intro"] else None,
              "synthetic_voice": voice, "loop": fx["loop"], "zoom_punches": len(fx["punches"]),
              "emoji": [e[1] for e in fx["emoji"]], "sfx": len(fx["sfx"])}
    return items, cap_words, overlays, fx, report, t

def _tts_words(text, t0, dur):
    ws = text.split()
    step = dur / max(1, len(ws))
    return [{"w": w, "s": t0 + k * step, "e": t0 + (k + 1) * step - 0.02} for k, w in enumerate(ws)]
