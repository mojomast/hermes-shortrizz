"""Select → picks.json → render.

A run's picks file is the contract a Desktop preview pane edits before any
encode. See ``docs/PICKS_CONTRACT.md``. ``picks.json`` is the current
revision; ``picks.vN.json`` is the immutable history.
"""
from __future__ import annotations

import json, re
from datetime import datetime, timezone
from pathlib import Path

from .util import log

SCHEMA_VERSION = 1
SNIPPET_CHARS = 280


def snippet(text, limit=SNIPPET_CHARS):
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _words_text(words, word_range):
    if not words or not word_range or len(word_range) != 2:
        return ""
    a, b = int(word_range[0]), int(word_range[1])
    if not 0 <= a <= b < len(words):
        return ""
    return " ".join(w["w"] for w in words[a:b + 1])


def picks_from_clips(clips, words=None, default_style="hormozi"):
    """One pick per selected clip. ``order`` starts at 1 in the list's order."""
    out = []
    for i, c in enumerate(clips, 1):
        text = c.get("transcript") or _words_text(words, c.get("word_range"))
        out.append({
            "id": f"p{i:02d}",
            "order": i,
            "enabled": True,
            "start": float(c["start"]),
            "end": float(c["end"]),
            "score": int(float(c.get("virality_score") or 0)),
            "title": (c.get("title") or "Clip").strip() or "Clip",
            "hook": c.get("why_it_hooks") or "",
            "transcript": snippet(text),
            "caption_style": c.get("caption_style") or default_style,
            "still": None,
            "caption": c.get("caption") or "",
            "hashtags": list(c.get("hashtags") or []),
            "method": c.get("method") or "",
            "word_range": list(c["word_range"]) if c.get("word_range") else None,
        })
    return out


def source_block(meta, src, duration):
    """Contract source object, plus the download metadata the gallery needs later."""
    meta = dict(meta or {})
    return {
        "title": meta.get("title") or "",
        "url": meta.get("webpage_url") or meta.get("source") or "",
        "video_path": str(src),
        "duration": round(float(duration), 3),
        "meta": meta,
    }


def build_document(*, run_id, kind, source, settings, clips, words=None, created=None):
    if kind not in ("shorts", "highlights"):
        raise ValueError("kind must be shorts or highlights")
    return {
        "version": SCHEMA_VERSION,
        "run_id": str(run_id),
        "kind": kind,
        "created": created or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "settings": settings,
        "picks": picks_from_clips(clips, words, (settings or {}).get("style") or "hormozi"),
    }


def still_frame(src, t, out_jpg, width=360):
    """One small JPEG from ``src`` at ``t`` seconds. ``-ss`` is before ``-i``."""
    from .render import still_frame as _still
    _still(src, t, out_jpg, width=width)


def write_stills(src, pick_list, run_dir, width=360):
    """Write ``stills/<id>.jpg`` for each pick. A failed frame leaves ``still`` null."""
    run_dir = Path(run_dir)
    for p in pick_list:
        dest = run_dir / "stills" / f"{p['id']}.jpg"
        t = max(0.0, (float(p["start"]) + float(p["end"])) / 2.0)
        try:
            still_frame(src, t, dest, width=width)
        except Exception as e:  # noqa: BLE001 — a missing still must not abort selection
            log(f"still for {p.get('id')} skipped ({e})")
            p["still"] = None
            dest.unlink(missing_ok=True)
            continue
        p["still"] = f"stills/{p['id']}.jpg"


def _revision_numbers(run_dir):
    nums = []
    for path in Path(run_dir).glob("picks.v*.json"):
        m = re.fullmatch(r"picks\.v(\d+)\.json", path.name)
        if m:
            nums.append(int(m.group(1)))
    return nums


def _canon(doc):
    data = json.loads(json.dumps(doc))
    data.pop("revision", None)
    return data


def _refresh_changed_stills(run_dir, prev, doc):
    src = ((doc.get("source") or {}).get("video_path"))
    if not src or not Path(src).is_file():
        return
    old = {p.get("id"): p for p in (prev or {}).get("picks") or []}
    changed = []
    for p in doc.get("picks") or []:
        was = old.get(p.get("id"))
        rel = p.get("still") or f"stills/{p['id']}.jpg"
        moved = was is None or was.get("start") != p.get("start") or was.get("end") != p.get("end")
        if moved or not (Path(run_dir) / rel).is_file():
            changed.append(p)
    if changed:
        write_stills(src, changed, run_dir)


def write_words(run_dir, words):
    """Sidecar of word timestamps so Studio can snap trims. Not part of the picks schema."""
    rows = []
    for w in words or []:
        if not isinstance(w, dict):
            continue
        try:
            rows.append({"w": str(w["w"]), "s": round(float(w["s"]), 3), "e": round(float(w["e"]), 3)})
        except (KeyError, TypeError, ValueError):
            continue
    path = Path(run_dir) / "words.json"
    path.write_text(json.dumps(rows, ensure_ascii=False))
    return path


def save_picks(run_dir, doc, *, refresh_stills=True):
    """Write ``picks.json`` and, when the content changed, ``picks.vN.json``.

    Returns the snapshot path. An unchanged document does not allocate a new
    revision. ``refresh_stills`` regenerates a JPEG when a pick's start or end
    changed, or when the file is missing.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    doc = json.loads(json.dumps(doc))
    latest = max(_revision_numbers(run_dir), default=0)
    prev = json.loads((run_dir / f"picks.v{latest}.json").read_text()) if latest else None
    if refresh_stills:
        _refresh_changed_stills(run_dir, prev, doc)
    if prev is not None and _canon(prev) == _canon(doc):
        text = json.dumps({**doc, "version": SCHEMA_VERSION, "revision": latest}, indent=2, ensure_ascii=False) + "\n"
        (run_dir / "picks.json").write_text(text)
        return run_dir / f"picks.v{latest}.json"
    n = latest + 1
    out_doc = {**doc, "version": SCHEMA_VERSION, "revision": n}
    text = json.dumps(out_doc, indent=2, ensure_ascii=False) + "\n"
    snap = run_dir / f"picks.v{n}.json"
    snap.write_text(text)
    (run_dir / "picks.json").write_text(text)
    return snap


def load_picks(path):
    """Load a picks file. Raises ValueError with a message a person can act on."""
    path = Path(path)
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise ValueError(f"could not read picks file {path}: {e}") from None
    if not isinstance(doc, dict):
        raise ValueError(f"{path} is not a picks document")
    if doc.get("version") != SCHEMA_VERSION:
        raise ValueError(
            f"{path} has picks version {doc.get('version')!r}; this build reads version {SCHEMA_VERSION}."
        )
    if doc.get("kind") not in ("shorts", "highlights"):
        raise ValueError(f"{path} kind must be shorts or highlights")
    if not isinstance(doc.get("source"), dict) or not (doc["source"].get("video_path")):
        raise ValueError(f"{path} is missing source.video_path")
    if not isinstance(doc.get("picks"), list):
        raise ValueError(f"{path} is missing the picks list")
    style = (doc.get("settings") or {}).get("style") or "hormozi"
    seen = set()
    for i, p in enumerate(doc["picks"]):
        if not isinstance(p, dict) or not p.get("id"):
            raise ValueError(f"pick {i} is missing an id")
        if p["id"] in seen:
            raise ValueError(f"duplicate pick id {p['id']}")
        seen.add(p["id"])
        try:
            start, end = float(p["start"]), float(p["end"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"pick {p['id']} needs numeric start and end") from None
        if end <= start:
            raise ValueError(f"pick {p['id']} has end ({end}) at or before start ({start})")
        p["start"], p["end"] = start, end
        p.setdefault("enabled", True)
        p.setdefault("order", i + 1)
        p.setdefault("score", 0)
        p.setdefault("title", "Clip")
        p.setdefault("hook", "")
        p.setdefault("transcript", "")
        p.setdefault("caption_style", style)
    return doc


def enabled_picks(doc):
    """Enabled picks, lowest ``order`` first. A missing ``enabled`` means on."""
    rows = [p for p in doc["picks"] if p.get("enabled", True)]
    rows.sort(key=lambda p: (int(p.get("order") or 0), str(p.get("id"))))
    return rows


def word_range_for(words, pick, start, end):
    """Word indices for a pick, honouring a trim without disturbing an unedited span.

    The stored ``word_range`` is kept when the media window still covers those
    words and has not grown far enough to swallow a neighbour (the selector
    pads a little silence around the words). A trim that cuts into the words
    drops them. A large extension falls through to every word inside the window.
    """
    if not words:
        return None
    stored = pick.get("word_range")
    if stored and len(stored) == 2:
        a, b = int(stored[0]), int(stored[1])
        if 0 <= a <= b < len(words):
            span_s, span_e = words[a]["s"], words[b]["e"]
            covers = start <= span_s + 0.05 and end >= span_e - 0.05
            grown_left = start < span_s - 0.35
            grown_right = end > span_e + 0.45
            if covers and not grown_left and not grown_right:
                while a <= b and words[a]["e"] <= start + 0.02:
                    a += 1
                while b >= a and words[b]["s"] >= end - 0.02:
                    b -= 1
                if a <= b:
                    return [a, b]
    lo, hi = start + 0.02, end - 0.02
    if hi <= lo:
        lo, hi = start, end
    inside = [i for i, w in enumerate(words) if w["e"] > lo and w["s"] < hi]
    return [inside[0], inside[-1]] if inside else None


def clips_for_render(doc, words):
    """Enabled picks in order, as the clip dicts the renderers already take.

    ``start``/``end`` are the trim, ``title`` and ``caption_style`` are the
    edited values, and ``hook`` is carried as ``why_it_hooks``.
    """
    chosen = []
    for p in enabled_picks(doc):
        start, end = float(p["start"]), float(p["end"])
        if end <= start:
            raise ValueError(f"pick {p.get('id')} has end ({end}) at or before start ({start})")
        wr = word_range_for(words, p, start, end)
        if wr is None:
            raise ValueError(f"pick {p.get('id')} has no words between {start:.2f}s and {end:.2f}s")
        a, b = wr
        text = " ".join(w["w"] for w in words[a:b + 1])
        style = p.get("caption_style") or (doc.get("settings") or {}).get("style") or "hormozi"
        chosen.append({
            "start": start, "end": end,
            "title": (p.get("title") or "Clip").strip() or "Clip",
            "virality_score": int(float(p.get("score") or 0)),
            "why_it_hooks": p.get("hook") or "",
            "caption": p.get("caption") or "",
            "hashtags": list(p.get("hashtags") or []),
            "method": p.get("method") or "picks",
            "word_range": [a, b],
            "caption_style": style,
            "transcript": text,
            "word_start": round(words[a]["s"], 3),
            "word_end": round(words[b]["e"], 3),
        })
        if p.get("hero") is True:
            chosen[-1]["hero"] = True
        hero_time = p.get("hero_time")
        if hero_time is not None:
            try:
                chosen[-1]["hero_time"] = float(hero_time)
            except (TypeError, ValueError):
                pass
    if not chosen:
        raise ValueError("no enabled picks to render")
    return chosen


def summaries(doc, run_dir):
    """Compact rows for ``shortrizz_status`` and ``--json``: stills are absolute paths."""
    run_dir = Path(run_dir)
    rows = []
    for p in doc.get("picks") or []:
        still = p.get("still")
        rows.append({
            "id": p.get("id"),
            "order": p.get("order"),
            "enabled": bool(p.get("enabled", True)),
            "start": p.get("start"),
            "end": p.get("end"),
            "score": p.get("score"),
            "title": p.get("title"),
            "hook": p.get("hook") or "",
            "caption_style": p.get("caption_style"),
            "still": str((run_dir / still).resolve()) if still else None,
        })
    return rows
