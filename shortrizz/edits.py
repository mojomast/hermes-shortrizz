"""Deeper edits: dead-air tightening, LLM word-index EDL, hook text, punch-ins, cold open, loops, TTS.

The LLM only ever proposes *word indices*; this module validates them and does all the timing work
(gap compression, padding, VAD-aligned cut points, crossfades). The EDL is saved next to each clip
(NAME.edl.json) and in the cache so `restyle` re-renders without another LLM call.
"""
import hashlib, json, os, re
from pathlib import Path
from .util import log

EDL_VERSION = 2

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
# words the LLM pass may remove on their own (as whole phrases); everything else needs to be a repeat or restart
FILLER_PHRASES = [("you", "know"), ("i", "mean"), ("sort", "of"), ("kind", "of"), ("um",), ("uh",), ("erm",), ("uhm",),
                  ("umm",), ("uhh",), ("hmm",), ("mm",), ("ah",), ("er",), ("like",), ("basically",), ("literally",),
                  ("actually",), ("right",), ("okay",), ("ok",), ("well",), ("so",), ("yeah",)]
# pronouns, connectives and meaning words: never cut alone, only as part of an exact repeat / restart
MEANING = set("""i me my mine you your yours he him his she her hers we us our ours they them their theirs it its
this that these those who whom whose which what where when why how and but or nor so because if then than though
although while unless since until is are was were be been am do does did have has had will would can could should
must may might all every only just very more most less least""".split())
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
- "drop": ONLY (a) filler words used as filler (um, uh, "you know", "I mean", "like" as a filler, "kind of", "sort of"),
  (b) exact stutter repeats ("that's that's" -> drop the first), (c) false starts the speaker restarts ("we went, we drove").
  Use index ranges w0..w1 inclusive. Never drop pronouns (I, you, it, which...), connectives (and, but, because, which is),
  numbers, negations (not/never/no/n't) or anything that changes meaning.
  Be conservative: at most {maxdrop} words total. If the speech is clean, return [].
- "hook_text": 2-6 word on-screen hook for the first 3 seconds that is TRUE to the clip (no clickbait), Title Case.
- "emphasis": up to {nemph} indices of the most important single words (for a punch-in zoom), spread out.
- "emoji": up to {nemoji} {{"w": index, "emoji": one emoji}} on concrete nouns/emotions, spread out.
- "cold_open": the most striking SHORT line to flash first: 4-12 consecutive words lasting 2-5 seconds. A clause or part
  of a sentence is fine if it lands on its own ("this is the trap"). Not the clip's first line. {{"w0","w1"}}. Always give one.
- "intro": one spoken narrator line, max 12 words, giving context (third person, no hype, don't impersonate the speaker).
- "cta": short end call to action, max 5 words (e.g. "Follow for part 2").
- "loop_end": index of the last word to keep so the ending flows back into the opening line, or null.
Return ONLY JSON: {{"drop":[{{"w0":i,"w1":j,"reason":"false_start|repeat|filler"}}],"hook_text":"...","emphasis":[i],
"emoji":[{{"w":i,"emoji":"🔥"}}],"cold_open":{{"w0":i,"w1":j}},"intro":"...","cta":"...","loop_end":i}}

Clip title: {title}
Words:
{lines}
"""

def _with_deadline(fn, seconds):
    """Hard wall-clock cap: requests' timeout is per read, so a trickling server could hang a render."""
    import concurrent.futures as cf
    ex = cf.ThreadPoolExecutor(1)
    try:
        return ex.submit(fn).result(timeout=seconds)
    except cf.TimeoutError:
        raise RuntimeError(f"LLM call exceeded {seconds}s")
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

def llm_plan(words, a, b, title):
    from .clipselect import llm_chat, parse_json
    dur = words[b]["e"] - words[a]["s"]
    lines = "\n".join(f"[{i}] {words[i]['w']} ({words[i]['s'] - words[a]['s']:.1f})" for i in range(a, b + 1))
    p = EDL_PROMPT.format(maxdrop=int((b - a + 1) * MAX_DROP_FRAC), nemph=max(2, int(dur / 6)),
                          nemoji=max(1, int(dur / 10)), title=title, lines=lines)
    msgs = [{"role": "user", "content": p}]
    for extra in ({"reasoning_effort": "low"}, {"thinking": {"type": "disabled"}}):
        try:
            return parse_json(_with_deadline(lambda: llm_chat(msgs, max_tokens=6000, timeout=60, extra=extra), 120))
        except Exception as e:  # noqa: BLE001 - retry once, then fall back to rules only
            log(f"  edit LLM failed ({e})")
    return {}

def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None

def _is_filler_run(toks, prev_word=""):
    """True if toks is made only of filler phrases ("you know", "right like", "um")."""
    i = 0
    while i < len(toks):
        for ph in sorted(FILLER_PHRASES, key=len, reverse=True):
            if tuple(toks[i:i + len(ph)]) == ph:
                if ph == ("like",) and not (prev_word.endswith(",") or len(toks) > 1):
                    return False  # "I like it" - only a filler when set off by a comma or with other filler
                i += len(ph); break
        else:
            return False
    return True

def drop_allowed(words, w0, w1, b, reason=""):
    """Only true fillers, exact repeats and restarts may go; protects pronouns/connectives/meaning words."""
    toks = [norm(words[k]["w"]) for k in range(w0, w1 + 1)]
    n = len(toks)
    nxt = [norm(words[k]["w"]) for k in range(w1 + 1, min(b, w1 + 6) + 1)]
    if nxt[:n] == toks:                       # exact repeat ("that's that's", "look at look at")
        return True
    if n <= 4 and toks[0] and toks[0] in nxt[:3] and words[w1]["e"] - words[w0]["s"] < 2.0 \
            and (reason == "false_start" or toks[0] in MEANING):
        return True                           # restart: "we went, we drove"
    if any(t in MEANING for t in toks) and not _is_filler_run(toks):
        return False
    prev = words[w0 - 1]["w"] if w0 > 0 else ""
    return _is_filler_run(toks, prev if not words[w0]["w"].endswith(",") else prev + ",")

def _clause_spans(words, a, b, mn=2.0, mx=5.0):
    """Candidate stand-alone clauses: break at punctuation, pauses >0.25 s and clause words."""
    brk = {"but", "because", "so", "and", "which", "then"}
    starts = [a] + [i for i in range(a + 1, b + 1) if words[i - 1]["w"][-1:] in ".,?!;:" or
                    words[i]["s"] - words[i - 1]["e"] > 0.25 or norm(words[i]["w"]) in brk]
    out = []
    for i0 in starts:
        for j in range(i0 + 3, min(b, i0 + 13) + 1):
            d = words[j]["e"] - words[i0]["s"]
            if d > mx:
                break
            end_ok = j == b or words[j]["w"][-1:] in ".,?!;:" or words[j + 1]["s"] - words[j]["e"] > 0.25
            if d >= mn and end_ok:
                out.append((i0, j))
    return out

_PUNCH = re.compile(r"\b(never|nobody|everyone|always|biggest|worst|best|secret|truth|mistake|wrong|insane|crazy|"
                    r"actually|problem|money|billion|million|\d+|why|how|not|won't|can't|only|real|trap|win|lose)\b", re.I)

def pick_cold_open(words, a, b, llm, taken=()):
    """LLM line if it is 1.5-5 s (trimmed to a clause if longer), else the punchiest 2-5 s clause."""
    first3 = words[a]["s"] + 3.0
    ok = lambda i0, j: (words[i0]["s"] >= first3 and not (set(range(i0, j + 1)) & set(taken)))
    c0, c1 = _int(llm.get("w0")), _int(llm.get("w1"))
    if c0 is not None and c1 is not None and a <= c0 <= c1 <= b:
        if words[c1]["e"] - words[c0]["s"] > 5.0:  # trim long picks to their punchiest sub-clause
            subs = [(i, j) for i, j in _clause_spans(words, c0, c1) if ok(i, j)]
            if subs:
                c0, c1 = max(subs, key=lambda ij: (len(_PUNCH.findall(_txt(words, *ij))), -(ij[1] - ij[0])))
        if 1.5 <= words[c1]["e"] - words[c0]["s"] <= 5.0 and ok(c0, c1):
            return {"w0": c0, "w1": c1, "text": _txt(words, c0, c1), "by": "llm"}
    cands = [(i, j) for i, j in _clause_spans(words, a, b) if ok(i, j)]
    if not cands:
        return None
    def score(ij):
        t = _txt(words, *ij)
        return (len(_PUNCH.findall(t)) * 2 + t.count("?") + t.count("!") - abs(words[ij[1]]["e"] - words[ij[0]]["s"] - 3.0) * 0.3)
    i0, j = max(cands, key=score)
    return {"w0": i0, "w1": j, "text": _txt(words, i0, j), "by": "heuristic"}

def _txt(words, i, j):
    return " ".join(words[k]["w"] for k in range(i, j + 1))

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
        if not drop_allowed(words, w0, w1, b, str(d.get("reason", ""))):
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
    cold = pick_cold_open(words, a, b, plan.get("cold_open") or {}, taken)
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

def build_segments(words, a, b, drops, policy, mask=None, loop_end=None, end_idx=None):
    """Return source-time segments [(s, e)] covering kept words, tightened by policy."""
    end_idx = loop_end if loop_end is not None else (end_idx if end_idx is not None else b)
    dropped = {k for d in drops for k in range(d["w0"], d["w1"] + 1)}
    kept = [i for i in range(a, end_idx + 1) if i not in dropped]
    if not kept:
        return [(words[a]["s"], words[b]["e"])], []
    if policy is None:
        return [(max(0.0, words[a]["s"] - 0.08), words[end_idx]["e"] + (0.04 if loop_end is not None else 0.12))], []
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
                                 opts.get("llm_edits"), tr_hash, EDL_VERSION]).encode()).hexdigest()[:10]
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
    drops = edl["drop"] if policy else []
    segs, cuts = build_segments(words, a, b, drops, policy, mask, loop_end)
    items, cap_words, t = [], [], 0.0
    fx = {"xfade_ms": (policy or {}).get("xfade_ms", 15), "bases": [], "punches": [], "drift": [], "sfx": [],
          "emoji": [], "loop": bool(loop_end is not None)}
    sfxdir = Path(__file__).parent / "assets" / "sfx"
    voice = False
    # --- narrator lines and cold open first, so the body can be trimmed to keep the length band
    intro_d = outro_d = None
    if opts["tts_intro"] and edl.get("intro"):
        from . import tts
        intro_wav = Path(workdir) / f"{name}.intro.wav"
        intro_d = tts.speak(edl["intro"], intro_wav)
    if opts["tts_outro"] and edl.get("cta") and loop_end is None:
        from . import tts
        outro_wav = Path(workdir) / f"{name}.outro.wav"
        outro_d = tts.speak(edl["cta"], outro_wav)
    cold = edl.get("cold_open") if opts["cold_open"] else None
    cold_d = (words[cold["w1"]]["e"] + 0.10 - max(0.0, words[cold["w0"]]["s"] - 0.06)) if cold else 0.0
    src_len = clip["end"] - clip["start"]
    budget = min(src_len, opts.get("max_len") or 1e9)   # never longer than the original or the length band
    floor = min(src_len, opts.get("min_len") or 0) * 0.9
    body = lambda sg: sum(e - s for s, e in sg)
    extras = lambda: (intro_d + 0.2 if intro_d else 0) + cold_d + (outro_d + 0.4 if outro_d else 0)
    trimmed_end = None
    if body(segs) + extras() > budget + 0.05:
        # give up extras (outro, then intro, then cold open) rather than gut the body below the band
        for k in ("outro", "intro", "cold"):
            if budget - extras() >= max(floor - extras(), 0.6 * body(segs)) or budget - extras() >= body(segs):
                break
            if k == "outro" and outro_d: outro_d = None
            elif k == "intro" and intro_d: intro_d = None
            elif k == "cold" and cold: cold, cold_d = None, 0.0
        target = budget - extras()
        if body(segs) > target + 0.05:
            j, segs2, cuts2 = trim_body(words, a, b, drops, policy, mask, target, loop_end is not None)
            if segs2:
                segs, cuts, trimmed_end = segs2, cuts2, j
                if loop_end is not None:
                    loop_end = j
    first_t = words[a]["s"]
    if intro_d:
        items.append({"kind": "hold", "dur": intro_d + 0.2, "wav": str(intro_wav), "at": first_t,
                      "window": (first_t, min(first_t + 4.0, clip["end"]))})
        fx["drift"].append((0.0, intro_d + 0.2)); t += intro_d + 0.2; voice = True
        cap_words += _tts_words(edl["intro"], 0.0, intro_d)
    if cold:
        c0, c1 = cold["w0"], cold["w1"]
        cs = (max(0.0, words[c0]["s"] - 0.06), words[c1]["e"] + 0.10)
        items.append({"kind": "src", "s": cs[0], "e": cs[1]})
        w, t = remap(words, range(c0, c1 + 1), [cs], t)
        cap_words += w
        fx["bases"].append((t - (cs[1] - cs[0]), t, 1.10 if opts["zoom"] else 1.0))
        if opts["sfx"]:
            fx["sfx"].append((max(0.0, t - 0.25), str(sfxdir / "whoosh.wav")))
    main0 = t
    dropped = {k for d in (edl["drop"] if policy else []) for k in range(d["w0"], d["w1"] + 1)}
    last_i = trimmed_end if trimmed_end is not None else (loop_end if loop_end is not None else b)
    w, t = remap(words, [i for i in range(a, last_i + 1) if i not in dropped], segs, t)
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
        # upper-left, under the hook text: off the (centred) face and inside the 120 px left safe margin
        fx["emoji_y"] = 660
    overlays = []
    if opts["hook_text"] and edl.get("hook_text"):
        overlays.append({"text": edl["hook_text"], "t0": 0.0, "t1": min(2.8, t), "pos": "top"})
    if outro_d:
        last_s = segs[-1][1] - 0.05
        items.append({"kind": "hold", "dur": outro_d + 0.4, "wav": str(outro_wav), "at": last_s,
                      "window": (max(segs[-1][0], last_s - 4.0), last_s)})
        fx["drift"].append((t, t + outro_d + 0.4))
        overlays.append({"text": edl["cta"], "t0": t, "t1": t + outro_d + 0.4, "pos": "middle"})
        t += outro_d + 0.4; voice = True
    elif (opts["loop"] or opts["tts_outro"]) and edl.get("cta"):
        overlays.append({"text": edl["cta"], "t0": max(0.0, t - 1.5), "t1": t, "pos": "middle"})
    main_src = sum(e - s for s, e in segs)
    report = {"source_sec": round(clip["end"] - clip["start"], 2), "kept_sec": round(main_src, 2),
              "removed_sec": round(clip["end"] - clip["start"] - main_src, 2), "cuts": len(cuts),
              "gap_cuts": sum(c["reason"] == "gap" for c in cuts), "drop_cuts": sum(c["reason"] == "drop" for c in cuts),
              "dropped_words": [d["text"] for d in edl["drop"]] if policy else [], "final_sec": round(t, 2),
              "hook_text": edl.get("hook_text") if opts["hook_text"] else None,
              "cold_open": cold["text"] if cold else None, "outro_tts": edl.get("cta") if outro_d else None,
              "trimmed_tail_sec": round(max(0.0, words[b]["e"] - words[trimmed_end]["e"]), 2) if trimmed_end is not None else 0.0,
              "budget_sec": round(budget, 2),
              "intro_tts": edl.get("intro") if intro_d else None,
              "synthetic_voice": voice, "loop": fx["loop"], "zoom_punches": len(fx["punches"]),
              "emoji": [e[1] for e in fx["emoji"]], "sfx": len(fx["sfx"])}
    return items, cap_words, overlays, fx, report, t

def trim_body(words, a, b, drops, policy, mask, target, loop=False):
    """Latest end word whose body fits target: prefer sentence ends, then pauses, then any word."""
    dropped = {k for d in drops for k in range(d["w0"], d["w1"] + 1)}
    lo = a + max(3, (b - a) // 3)
    tiers = ([], [], [])
    for j in range(b - 1, lo - 1, -1):
        if j in dropped:
            continue
        w = words[j]["w"]
        tier = 0 if w[-1:] in ".?!" else 1 if (w[-1:] in ",;:" or words[j + 1]["s"] - words[j]["e"] > 0.3) else 2
        tiers[tier].append(j)
    best = []
    for tier in tiers:  # latest fitting end word in each tier
        for j in tier:
            sg, cu = build_segments(words, a, b, drops, policy, mask, j if loop else None, end_idx=j)
            if sum(e - s for s, e in sg) <= target + 0.05:
                best.append((j, sg, cu)); break
        else:
            best.append(None)
    for tier, slack in ((0, 4.0), (1, 2.0), (2, 1e9)):  # a sentence end may cost up to 4 s, a pause up to 2 s
        if best[tier] and sum(e - s for s, e in best[tier][1]) >= target - slack:
            return best[tier]
    return next((x for x in best if x), (None, None, None))

def _tts_words(text, t0, dur):
    ws = text.split()
    step = dur / max(1, len(ws))
    return [{"w": w, "s": t0 + k * step, "e": t0 + (k + 1) * step - 0.02} for k, w in enumerate(ws)]


# ------------------------------------------------------------------ face-aware text placement
def _faces_between(track, t0, t1):
    bx = [b for t, boxes in track if t0 - 0.5 <= t <= t1 + 0.25 for b in boxes]
    return bx

def caption_y_fn(track, base_y, size, hook_until=0.0):
    """Caption centre y per page: keep the default unless it overlaps a face, then go below the chin
    (if still above the bottom safe line) or above the forehead (below the top safe zone / hook)."""
    half = size * 0.62
    lo_lim = SAFE["bottom_y"] - half            # lowest allowed centre
    def overlap(y, boxes):
        return sum(max(0.0, min(y + half, b[3] + 10) - max(y - half, b[1] - 10)) for b in boxes)
    def y_at(t0, t1):
        boxes = [b for b in _faces_between(track, t0, t1) if (b[2] - b[0]) > 90]
        if not boxes or overlap(base_y, boxes) == 0:
            return base_y
        top_lim = SAFE["top"] + half + (260 if t0 < hook_until else 40)
        fy0, fy1 = min(b[1] for b in boxes), max(b[3] for b in boxes)
        cands = [base_y, min(lo_lim, fy1 + half + 24), max(top_lim, fy0 - half - 24)]
        cands = [min(lo_lim, max(top_lim, c)) for c in cands]
        return int(min(cands, key=lambda c: (overlap(c, boxes), abs(c - base_y))))
    return y_at

EMOJI_SPOTS = [(250, 660), (830 - 75, 660), (250, 880), (830 - 75, 880), (540, 560)]

def emoji_places(track, items, cap_y_fn, size=150):
    """Choose, for each emoji pop, the first spot that clears every face box and the caption line."""
    out = {}
    for t0, _ in items:
        boxes = _faces_between(track, t0, t0 + 1.2)
        cy = cap_y_fn(t0, t0 + 1.2) if cap_y_fn else 1160
        def clear(p):
            x, y = p
            r = (x - size / 2 - 12, y - size / 2 - 12, x + size / 2 + 12, y + size / 2 + 12)
            if r[3] > cy - 80 and r[1] < cy + 80:
                return False
            return not any(r[0] < b[2] and b[0] < r[2] and r[1] < b[3] and b[1] < r[3] for b in boxes)
        spot = next((p for p in EMOJI_SPOTS if clear(p)), None)
        if spot is not None:
            out[t0] = spot
        else:
            out[t0] = None   # no clean spot: skip this emoji rather than cover a face
    return out
