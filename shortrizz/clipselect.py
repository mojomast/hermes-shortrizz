"""Pick the best 30-60 s moments: LLM (OpenCode Go, OpenAI-compatible) with a heuristic fallback.

Highlights reuses the same candidates, snap-to-sentence and virality scores, then keeps a
non-overlapping subset that fits a target duration.
"""
import difflib, json, os, re, uuid
from pathlib import Path
from .secrets import get_secret, DECLARED_SECRETS
from .util import log
from .transcribe import all_words

BASE_URL = os.environ.get("SHORTRIZZ_LLM_BASE_URL", "https://opencode.ai/zen/go/v1")
MODEL = os.environ.get("SHORTRIZZ_LLM_MODEL", "deepseek-v4.1-flash")
KEY_VAR = DECLARED_SECRETS[0]
END_PUNCT = (".", "?", "!")

# ---------------------------------------------------------------- boundaries
def _gap_before(words, i):
    return words[i]["s"] - words[i - 1]["e"] if i > 0 else 9.0

def _gap_after(words, i):
    return words[i + 1]["s"] - words[i]["e"] if i + 1 < len(words) else 9.0

def start_rank(words, i):
    """0 = sentence start, 1 = segment start / pause, 2 = any word."""
    if i == 0 or words[i - 1]["w"].endswith(END_PUNCT):
        return 0
    if words[i].get("ss") or _gap_before(words, i) >= 0.35 or words[i - 1]["w"].endswith((",", ";", ":")):
        return 1
    return 2

def end_rank(words, j):
    if words[j]["w"].endswith(END_PUNCT):
        return 0
    if words[j].get("se") or _gap_after(words, j) >= 0.35:
        return 1
    return 2

def snap(words, start, end, mn, mx, window=4.0):
    """Snap (start,end) seconds to the best nearby sentence/phrase boundaries, keeping mn..mx length.

    Prefers true sentence boundaries, then Whisper segment edges or pauses, then any word
    (Whisper sometimes emits almost no punctuation). Start must lie within `window` s of the request.
    Returns (i0, j) word indices or None."""
    if not words:
        return None
    near = [i for i, w in enumerate(words) if abs(w["s"] - start) <= window]
    if not near:
        near = [min(range(len(words)), key=lambda i: abs(words[i]["s"] - start))]
    i0 = min(near, key=lambda i: (start_rank(words, i), abs(words[i]["s"] - start)))
    t0 = words[i0]["s"]
    cand = [j for j in range(i0, len(words)) if mn <= words[j]["e"] - t0 <= mx]
    if not cand:
        return None
    j = min(cand, key=lambda j: (end_rank(words, j), abs(words[j]["e"] - end)))
    return i0, j

def sentence_starts(words):
    return [i for i in range(len(words)) if start_rank(words, i) <= 1]

def sentence_ends(words):
    return [j for j in range(len(words)) if end_rank(words, j) <= 1]

def transcript_text(tr, max_chars=60000):
    lines = [f"[{s['s']:.1f}-{s['e']:.1f}] {s['text']}" for s in tr["segments"]]
    txt = "\n".join(lines)
    if len(txt) > max_chars:  # very long videos: keep evenly spaced chunk (simple)
        step = len(txt) / max_chars
        lines = lines[::max(1, int(step + 0.999))]
        txt = "\n".join(lines)
    return txt

# ---------------------------------------------------------------- LLM
PROMPT = """You are a viral short-form video editor (like Opus Clip). From the timestamped transcript below, pick the {n} best standalone moments for YouTube Shorts / TikTok / Reels.

Rules:
- Each clip must be {mn}-{mx} seconds long.
- START on a strong hook (a bold claim, surprising fact, question, conflict, or emotional line) - never mid-sentence, no throat-clearing.
- END on a complete thought / payoff, never mid-sentence.
- Clips must not overlap. Must make sense without the rest of the video.
- virality_score: integer 0-100, be honest and spread scores out.

Video title: {title}

Return ONLY JSON: {{"clips": [{{"start": seconds, "end": seconds, "title": "punchy title <= 60 chars", "virality_score": int, "why_it_hooks": "1-2 sentences", "caption": "platform caption 1-2 sentences with a call to action", "hashtags": ["#tag", ...5-8]}}]}}

Transcript:
{transcript}
"""

def llm_chat(messages, max_tokens=16000, timeout=300, extra=None):
    import requests
    key = get_secret(KEY_VAR)
    if not key:
        raise RuntimeError(f"{KEY_VAR} is not set")
    r = requests.post(f"{BASE_URL}/chat/completions", timeout=timeout,
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                               "x-opencode-session": f"shortrizz-{uuid.uuid4().hex[:12]}"},
                      json={"model": MODEL, "messages": messages, "max_tokens": max_tokens,
                            "temperature": 0.4, **(extra or {})})
    if r.status_code != 200:
        raise RuntimeError(f"LLM HTTP {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"].get("content") or ""

def parse_json(txt):
    txt = re.sub(r"^```(json)?|```$", "", txt.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", txt, re.S)
    return json.loads(m.group(0) if m else txt)

def llm_candidates(tr, n, mn, mx, title):
    p = PROMPT.format(n=n, mn=mn, mx=mx, title=title, transcript=transcript_text(tr))
    log(f"asking LLM ({MODEL}) for {n} candidates…")
    msgs = [{"role": "user", "content": p}]
    try:
        return parse_json(llm_chat(msgs, extra={"reasoning_effort": "low"})).get("clips", [])
    except (ValueError, AttributeError) as e:
        log(f"LLM reply unparsable ({e}); retrying with thinking disabled")
        return parse_json(llm_chat(msgs, extra={"thinking": {"type": "disabled"}})).get("clips", [])

# ---------------------------------------------------------------- heuristic
HOOK = re.compile(r"\b(you|your|secret|never|always|most|biggest|mistake|why|how|truth|nobody|everyone|"
                  r"money|million|\d+|best|worst|actually|problem|stop|important|learned)\b", re.I)

def heuristic_candidates(tr, n, mn, mx):
    words = all_words(tr)
    out = []
    from .transcribe import is_looping
    for i0 in sentence_starts(words):
        sn = snap(words, words[i0]["s"], words[i0]["s"] + (mn + mx) / 2, mn, mx)
        if not sn:
            continue
        a, b = sn
        seg = words[a:b+1]
        text = " ".join(w["w"] for w in seg)
        if is_looping(text):
            continue
        first = " ".join(w["w"] for w in seg[:12])
        dur = seg[-1]["e"] - seg[0]["s"]
        wps = len(seg) / max(dur, 1)
        score = (len(HOOK.findall(first)) * 8 + len(HOOK.findall(text)) * 1.5 + (10 if "?" in first else 0)
                 + min(wps, 3.5) * 8)
        out.append({"start": seg[0]["s"], "end": seg[-1]["e"], "raw": score,
                    "title": " ".join(w["w"] for w in seg[:8]).strip(" ,.") + "…",
                    "why_it_hooks": "Heuristic pick: dense speech with hook words early in the clip.",
                    "caption": first.strip() + "…", "hashtags": ["#shorts", "#podcast", "#clips"]})
    out.sort(key=lambda c: -c["raw"])
    if out:
        hi = out[0]["raw"]
        for c in out:
            c["virality_score"] = int(40 + 50 * c["raw"] / hi)
    return out

def sane_clip(seg_words, title=""):
    """Reject clips whose speech or title is a repetition loop or too sparse to caption."""
    from .transcribe import is_looping
    text = " ".join(w["w"] for w in seg_words)
    dur = seg_words[-1]["e"] - seg_words[0]["s"] if seg_words else 0
    if len(seg_words) < max(20, dur * 0.8):  # < ~50 wpm: barely any speech
        return False
    return not is_looping(text) and not (title and is_looping(title + " " + title))

def clean_tags(tags, limit=8):
    """'#tag' form, no spaces, de-duplicated case-insensitively, at most `limit`."""
    out, seen = [], set()
    for t in tags or []:
        t = "#" + re.sub(r"[^\w]", "", str(t).lstrip("#"))
        if len(t) > 1 and t.lower() not in seen:
            seen.add(t.lower()); out.append(t)
    return out[:limit]

# ---------------------------------------------------------------- main
def gather_candidates(tr, llm_n, heur_n, mn, mx, title="", use_llm=True, cache_file=None):
    """Scored raw candidates from the LLM (cached) or the heuristic. Shared by shorts and highlights."""
    cands, method = [], "heuristic"
    if use_llm and cache_file and Path(cache_file).exists():
        cands, method = json.loads(Path(cache_file).read_text()), "llm"
        log(f"using cached LLM picks {cache_file}")
    elif use_llm:
        try:
            cands = llm_candidates(tr, llm_n, mn, mx, title); method = "llm"
            if cache_file and cands:
                Path(cache_file).write_text(json.dumps(cands))
        except Exception as e:
            log(f"LLM selection failed ({e}); using heuristic fallback")
    if not cands:
        cands = heuristic_candidates(tr, heur_n, mn, mx)
    return cands, method

def select(tr, count, mn, mx, title="", use_llm=True, cache_file=None, exclude=()):
    """exclude: [(start, end)] source ranges used by earlier runs; overlapping candidates are skipped."""
    words = all_words(tr)
    cands, method = gather_candidates(tr, count * 2, count * 3, mn, mx, title, use_llm, cache_file)
    picked = []
    for c in sorted(cands, key=lambda c: -float(c.get("virality_score", 0))):
        try:
            sn = snap(words, float(c["start"]), float(c["end"]), mn, mx)
        except (KeyError, ValueError, TypeError):
            continue
        if not sn:
            continue
        a, b = sn
        s, e = words[a]["s"], words[b]["e"]
        if any(s < p["end"] and e > p["start"] for p in picked):
            continue
        if any(s < xe and e > xs for xs, xe in exclude):
            continue
        if not sane_clip(words[a:b + 1], c.get("title", "")):
            log(f"  skipping degenerate candidate {c.get('title', '')[:40]!r}")
            continue
        tags = clean_tags(c.get("hashtags", []))
        picked.append({"start": round(max(0, s - 0.08), 2), "end": round(e + 0.25, 2),
                       "title": c.get("title", "Clip").strip(), "virality_score": int(c.get("virality_score", 50)),
                       "why_it_hooks": c.get("why_it_hooks", ""), "caption": c.get("caption", ""),
                       "hashtags": tags, "word_range": [a, b], "method": method})
        if len(picked) >= count:
            break
    if len(picked) < count and method == "llm":
        log("LLM gave too few valid clips; topping up with heuristic")
        for c in heuristic_candidates(tr, count * 3, mn, mx):
            if len(picked) >= count:
                break
            if any(c["start"] < p["end"] and c["end"] > p["start"] for p in picked):
                continue
            if any(c["start"] < xe and c["end"] > xs for xs, xe in exclude):
                continue
            sn = snap(words, c["start"], c["end"], mn, mx); a, b = sn
            if not sane_clip(words[a:b + 1], c["title"]):
                continue
            picked.append({**{k: c[k] for k in ("title", "virality_score", "why_it_hooks", "caption", "hashtags")},
                           "start": round(max(0, words[a]["s"] - 0.08), 2), "end": round(words[b]["e"] + 0.25, 2),
                           "word_range": [a, b], "method": "heuristic"})
    return picked

# ---------------------------------------------------------------- highlights
def media_span(words, a, b, pre=0.12, post=0.18, guard=0.04):
    """Source in/out for words[a..b], padded into silence but never into a neighbouring word."""
    s = words[a]["s"] - pre
    if a > 0:
        s = max(s, words[a - 1]["e"] + guard)
    s = min(max(0.0, s), words[a]["s"])
    e = words[b]["e"] + post
    if b + 1 < len(words):
        e = min(e, words[b + 1]["s"] - guard)
    e = max(e, words[b]["e"])
    if e < s:
        s, e = words[a]["s"], words[b]["e"]
    return round(s, 3), round(e, 3)

def _flat(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()

def transcript_similarity(a, b):
    ta, tb = _flat(a), _flat(b)
    if not ta or not tb:
        return 0.0
    return difflib.SequenceMatcher(None, ta, tb).ratio()

def near_duplicate(a, b, similarity=0.86):
    """True when two segments replay the same moment or nearly the same words."""
    a0, a1 = float(a.get("word_start", a["start"])), float(a.get("word_end", a["end"]))
    b0, b1 = float(b.get("word_start", b["start"])), float(b.get("word_end", b["end"]))
    if a0 < b1 - 0.05 and b0 < a1 - 0.05:
        return True
    return transcript_similarity(a.get("transcript", ""), b.get("transcript", "")) >= similarity

def _fits(total, dur, target, slack):
    if not target or target <= 0:
        return True
    if total >= target - 1e-6:
        return False
    return total + dur <= target + slack

def choose_highlights(clips, target=180, max_clips=8, min_score=50, order="chrono",
                      overshoot=0.12, similarity=0.86):
    """Pick whole segments, highest score first, until the duration or the count is used up.

    A segment is skipped when it overlaps or near-duplicates one already kept, when it
    scores below `min_score`, or when adding it would run past `target` by more than
    `overshoot` (and at least 4 s). Nothing is sliced mid-thought to hit the number.
    `target` or `max_clips` of 0 turns that limit off. With both off, the caller should
    set a count. If nothing fits the budget, the single highest-scoring segment is kept.
    """
    if order not in ("chrono", "score"):
        raise ValueError("order must be chrono or score")
    ranked = sorted(clips, key=lambda c: (-float(c.get("virality_score", 0)), float(c.get("start", 0))))
    slack = max(4.0, float(target) * overshoot) if target and target > 0 else 0.0
    picked, total = [], 0.0
    for c in ranked:
        if float(c.get("virality_score", 0)) < float(min_score):
            break
        if max_clips and len(picked) >= max_clips:
            break
        dur = float(c["end"]) - float(c["start"])
        if dur < 0.8:
            continue
        if any(near_duplicate(c, p, similarity) for p in picked):
            continue
        if not _fits(total, dur, target, slack):
            continue
        picked.append(c)
        total += dur
    if not picked and ranked:
        eligible = [c for c in ranked if float(c.get("virality_score", 0)) >= float(min_score)
                    and float(c["end"]) - float(c["start"]) >= 0.8]
        if eligible:
            log(f"no segment fits the highlight budget; keeping the top score ({eligible[0].get('virality_score')})")
            picked = [eligible[0]]
    if order == "score":
        picked.sort(key=lambda c: (-float(c["virality_score"]), float(c["start"])))
    else:
        picked.sort(key=lambda c: float(c["start"]))
    return picked

def separate_ranges(segments):
    """Trim silence padding so source ranges don't overlap. Word cores stay intact. Order is kept."""
    out = [dict(s) for s in segments]
    order = sorted(range(len(out)), key=lambda i: (out[i]["start"], out[i]["end"]))
    for i, j in zip(order, order[1:]):
        a, b = out[i], out[j]
        if a["end"] <= b["start"] + 1e-3:
            continue
        a_floor = a.get("word_end", a["end"])
        b_ceil = b.get("word_start", b["start"])
        if a_floor <= b_ceil:
            meet = min(b_ceil, max(a_floor, (a["end"] + b["start"]) / 2))
            a["end"] = round(min(a["end"], max(a_floor, meet)), 3)
            b["start"] = round(max(b["start"], min(b_ceil, a["end"])), 3)
            if a["end"] > b["start"]:
                a["end"] = round(b["start"], 3)
        a["end"] = round(a["end"], 3)
        b["start"] = round(b["start"], 3)
    return out

def _realize(words, c, mn, mx, method, exclude):
    try:
        sn = snap(words, float(c["start"]), float(c["end"]), mn, mx)
    except (KeyError, ValueError, TypeError):
        return None
    if not sn:
        return None
    a, b = sn
    seg = words[a:b + 1]
    if not sane_clip(seg, c.get("title", "")):
        return None
    ws, we = seg[0]["s"], seg[-1]["e"]
    if any(ws < xe and we > xs for xs, xe in exclude):
        return None
    s, e = media_span(words, a, b)
    if e - s < 0.8:
        return None
    text = " ".join(w["w"] for w in seg)
    return {"start": s, "end": e, "word_start": round(ws, 3), "word_end": round(we, 3),
            "title": (c.get("title") or "Highlight").strip(),
            "virality_score": int(float(c.get("virality_score", 50))),
            "why_it_hooks": c.get("why_it_hooks", ""), "caption": c.get("caption", ""),
            "hashtags": clean_tags(c.get("hashtags", [])), "word_range": [a, b],
            "method": c.get("method", method), "transcript": text}

def _underfilled(chosen, target, max_clips):
    total = sum(max(0.0, c["end"] - c["start"]) for c in chosen)
    room = not max_clips or max_clips <= 0 or len(chosen) < max_clips
    if not room:
        return False
    if target and target > 0:
        return total < target * 0.85
    return len(chosen) < (max_clips or 1)

def select_highlights(tr, mn, mx, target=180, max_clips=8, min_score=50, order="chrono", title="",
                      use_llm=True, cache_file=None, exclude=(), overshoot=0.12, similarity=0.86):
    """Top-scoring, non-overlapping, sentence-bounded segments for one highlights reel.

    Same candidate source as `select` (LLM, then the heuristic). Selection is by score;
    `order` only changes the sequence they play in (`chrono` keeps the story in order).
    """
    words = all_words(tr)
    if not words:
        return []
    if (not target or target <= 0) and (not max_clips or max_clips <= 0):
        max_clips = 8
    ask = max(12, int(max_clips or 8) * 3)
    cands, method = gather_candidates(tr, ask, ask, mn, mx, title, use_llm, cache_file)
    pool = []
    for c in cands:
        realized = _realize(words, c, mn, mx, method, exclude)
        if realized:
            pool.append(realized)
    kw = dict(target=target or 0, max_clips=max_clips or 0, min_score=min_score, order=order,
              overshoot=overshoot, similarity=similarity)
    chosen = choose_highlights(pool, **kw)
    if method == "llm" and _underfilled(chosen, target or 0, max_clips or 0):
        log("LLM gave too little highlight material; topping up with heuristic")
        extra, _ = gather_candidates(tr, ask, ask, mn, mx, title, use_llm=False)
        for c in extra:
            realized = _realize(words, c, mn, mx, "heuristic", exclude)
            if realized:
                pool.append(realized)
        chosen = choose_highlights(pool, **kw)
    chosen = separate_ranges(chosen)
    if order == "score":
        chosen.sort(key=lambda c: (-float(c["virality_score"]), float(c["start"])))
    else:
        chosen.sort(key=lambda c: float(c["start"]))
    total = sum(c["end"] - c["start"] for c in chosen)
    log(f"highlight reel: {len(chosen)} segment(s), {total:.0f}s, order {order}")
    for c in chosen:
        log(f"  {c['start']:.1f}-{c['end']:.1f}s  score {c['virality_score']}  {c['title']}")
    return chosen
