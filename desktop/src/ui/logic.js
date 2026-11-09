/** Pure storyboard helpers. No React, no host, no network. */

export const STAGE_LABEL = {
  selecting: "Selecting",
  picks_ready: "Picks ready",
  rendering: "Rendering",
  done: "Done",
  error: "Error",
};

export const STYLE_META = {
  hormozi: { upper: true, maxWords: 3, className: "sr-cap-hormozi" },
  mrbeast: { upper: true, maxWords: 2, className: "sr-cap-mrbeast" },
  abdaal: { upper: false, maxWords: 4, className: "sr-cap-abdaal" },
};

export const FALLBACK_STYLES = [
  { id: "hormozi", description: "Bold uppercase, yellow active word." },
  { id: "mrbeast", description: "Chunky type, green / yellow / red active word." },
  { id: "abdaal", description: "Sentence case, blue glow on the active word." },
];

export function round3(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.round(n * 1000) / 1000;
}

export function snapEdge(words, time, edge, slack = 0.45) {
  const t = Number(time);
  if (!Number.isFinite(t)) return 0;
  if (!Array.isArray(words) || !words.length) return round3(t);
  let best = null;
  let bestDist = slack + 1;
  for (const word of words) {
    const point = Number(edge === "end" ? word.e : word.s);
    if (!Number.isFinite(point)) continue;
    const dist = Math.abs(point - t);
    if (dist < bestDist - 1e-9 || (best !== null && Math.abs(dist - bestDist) < 1e-9 && point < best)) {
      best = point;
      bestDist = dist;
    }
  }
  if (best === null || bestDist > slack + 1e-9) return round3(t);
  return round3(best);
}

export function nudgeEdge(words, time, edge, delta) {
  return snapEdge(words, Number(time) + Number(delta), edge, 0.55);
}

export function reorderPicks(picks, fromId, toId) {
  const next = (picks || []).map((pick) => ({ ...pick }));
  const from = next.findIndex((pick) => pick.id === fromId);
  const to = next.findIndex((pick) => pick.id === toId);
  if (from < 0 || to < 0 || from === to) {
    return next.map((pick, index) => ({ ...pick, order: index + 1 }));
  }
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next.map((pick, index) => ({ ...pick, order: index + 1 }));
}

export function picksFingerprint(picks) {
  return JSON.stringify((picks || []).map((pick) => [
    pick.id,
    pick.order,
    pick.enabled !== false,
    round3(pick.start),
    round3(pick.end),
    String(pick.title || ""),
    String(pick.caption_style || ""),
  ]));
}

export function clonePick(pick) {
  return {
    id: pick.id,
    order: pick.order,
    enabled: pick.enabled !== false,
    start: round3(pick.start),
    end: round3(pick.end),
    score: pick.score,
    title: pick.title || "Clip",
    hook: pick.hook || "",
    transcript: pick.transcript || "",
    caption_style: pick.caption_style || "hormozi",
    still: pick.still || "",
    words: Array.isArray(pick.words) ? pick.words.map((word) => ({ ...word })) : [],
  };
}

export function fmtClock(sec) {
  const n = Math.max(0, Number(sec) || 0);
  const minutes = Math.floor(n / 60);
  const seconds = (n - minutes * 60).toFixed(1).padStart(4, "0");
  return minutes + ":" + seconds;
}

export function fmtDuration(start, end) {
  const n = Math.max(0, Number(end) - Number(start));
  return n.toFixed(1) + "s";
}

export function progressLabel(stage, progress) {
  const done = Number(progress && progress.done) || 0;
  const total = Number(progress && progress.total) || 0;
  if (stage === "rendering" && total) return "Clip " + Math.min(total, done + 1) + " of " + total;
  if (stage === "selecting") return total ? "Selecting " + done + " / " + total : "Transcribing and picking moments";
  if (stage === "picks_ready") return "Ready to review — nothing encoded yet";
  if (stage === "done") return "Rendered";
  if (stage === "error") return "Stopped before it finished";
  return "";
}

export function depNotes(deps) {
  if (!deps) return [];
  const notes = [];
  if (!deps.ffmpeg || !deps.ffprobe) {
    notes.push({
      tone: "bad",
      text: "ffmpeg and ffprobe must be on PATH before Studio can select or render.",
    });
  }
  if (!deps.yt_dlp) {
    notes.push({
      tone: "warn",
      text: "yt-dlp is not installed. A URL needs it. A local file does not.",
    });
  }
  const key = deps.opencode_go_api_key;
  if (key && key.set === false) {
    notes.push({
      tone: "info",
      text: key.name + " is not set. Studio will use the heuristic picker.",
    });
  }
  return notes;
}

function formatToken(text, upper) {
  const raw = String(text || "").trim();
  if (!upper) return raw;
  return raw.replace(/[.,!?;:]+$/g, "").toUpperCase();
}

export function captionLine(pick, at) {
  const meta = STYLE_META[pick && pick.caption_style] || STYLE_META.hormozi;
  const start = Number(pick && pick.start) || 0;
  const end = Number(pick && pick.end) || 0;
  const words = (pick && pick.words) || [];
  const inWindow = words.filter((word) => Number(word.e) > start - 0.05 && Number(word.s) < end + 0.05);
  let active = 0;
  if (inWindow.length && Number.isFinite(at)) {
    const found = inWindow.findIndex((word) => at + 1e-6 >= Number(word.s) && at <= Number(word.e) + 0.08);
    if (found >= 0) active = found;
    else {
      active = 0;
      for (let i = 0; i < inWindow.length; i += 1) {
        if (Number(inWindow[i].s) <= at) active = i;
      }
    }
  } else if (inWindow.length) {
    active = Math.min(inWindow.length - 1, Math.floor(inWindow.length / 2));
  }
  let tokens = [];
  if (inWindow.length) {
    const pageStart = Math.floor(active / meta.maxWords) * meta.maxWords;
    const page = inWindow.slice(pageStart, pageStart + meta.maxWords);
    tokens = page.map((word, index) => ({
      text: formatToken(word.w, meta.upper),
      on: pageStart + index === active,
    }));
  } else {
    const parts = String((pick && pick.transcript) || "").split(/\s+/).filter(Boolean).slice(0, meta.maxWords);
    const mid = Math.max(0, Math.floor((parts.length - 1) / 2));
    tokens = parts.map((text, index) => ({ text: formatToken(text, meta.upper), on: index === mid }));
  }
  if (!tokens.length) tokens = [{ text: meta.upper ? "CAPTION" : "Caption", on: true }];
  return { className: meta.className, tokens };
}

export function previewKey(pick) {
  return "preview:" + pick.id + ":" + round3(pick.start) + ":" + round3(pick.end);
}

export function mediaUrl(media, key) {
  const value = media && media[key];
  return typeof value === "string" && value && value !== "pending" ? value : "";
}
