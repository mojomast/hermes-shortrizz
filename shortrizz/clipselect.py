"""Pick the best 30-60 s moments: LLM (OpenCode Go, OpenAI-compatible) with a heuristic fallback."""
import json, os, re, uuid
from pathlib import Path
from .util import log, load_env_file
from .transcribe import all_words

BASE_URL = os.environ.get("SHORTRIZZ_LLM_BASE_URL", "https://opencode.ai/zen/go/v1")
MODEL = os.environ.get("SHORTRIZZ_LLM_MODEL", "deepseek-v4.1-flash")
ENV_FILE = os.environ.get("SHORTRIZZ_ENV_FILE", os.path.join(os.environ.get("HERMES_HOME", "~/.hermes"), ".env"))
KEY_VAR = "OPENCODE_GO_API_KEY"
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
    load_env_file(ENV_FILE)
    key = os.environ.get(KEY_VAR)
    if not key:
        raise RuntimeError(f"{KEY_VAR} not set (looked in {ENV_FILE})")
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
def select(tr, count, mn, mx, title="", use_llm=True, cache_file=None, exclude=()):
    """exclude: [(start, end)] source ranges used by earlier runs; overlapping candidates are skipped."""
    words = all_words(tr)
    cands, method = [], "heuristic"
    if use_llm and cache_file and Path(cache_file).exists():
        cands, method = json.loads(Path(cache_file).read_text()), "llm"
        log(f"using cached LLM picks {cache_file}")
    elif use_llm:
        try:
            cands = llm_candidates(tr, count * 2, mn, mx, title); method = "llm"
            if cache_file and cands:
                Path(cache_file).write_text(json.dumps(cands))
        except Exception as e:
            log(f"LLM selection failed ({e}); using heuristic fallback")
    if not cands:
        cands = heuristic_candidates(tr, count * 3, mn, mx)
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
