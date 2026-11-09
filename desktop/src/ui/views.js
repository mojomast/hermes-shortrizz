import { jsx, jsxs } from "react/jsx-runtime";
import {
  FALLBACK_STYLES,
  STAGE_LABEL,
  captionLine,
  fmtClock,
  fmtDuration,
  mediaUrl,
  previewKey,
  progressLabel,
} from "./logic.js";

function cx(...parts) {
  return parts.filter(Boolean).join(" ");
}

export function DepNotes({ notes }) {
  if (!notes || !notes.length) return null;
  return jsx("div", {
    className: "sr-notes",
    children: notes.map((note) => jsx("p", {
      className: cx("sr-note", "is-" + note.tone),
      children: note.text,
    }, note.text)),
  });
}

export function Onboarding({ source, kind, style, styles, busy, blocked, onSource, onKind, onStyle, onStart }) {
  const options = styles && styles.length ? styles : FALLBACK_STYLES;
  return jsxs("form", {
    className: "sr-onboard",
    "data-testid": "onboarding",
    onSubmit: (event) => {
      event.preventDefault();
      onStart();
    },
    children: [
      jsx("p", { className: "sr-kicker", children: "Shortrizz Studio" }),
      jsx("h2", { className: "sr-headline", children: "Pick the moments first." }),
      jsx("p", { className: "sr-lead", children: "Paste a video. Studio transcribes it, lays out a storyboard, and waits. Nothing is encoded until you render." }),
      jsxs("label", {
        className: "sr-field",
        children: [
          jsx("span", { children: "Video URL or local file" }),
          jsx("input", {
            value: source,
            placeholder: "https://… or /path/to/video.mp4",
            onChange: (event) => onSource(event.target.value),
            autoComplete: "off",
            spellCheck: false,
          }),
        ],
      }),
      jsxs("div", {
        className: "sr-seg",
        role: "group",
        "aria-label": "Output",
        children: [
          jsx("button", { type: "button", className: kind === "shorts" ? "is-on" : "", onClick: () => onKind("shorts"), children: "Shorts" }),
          jsx("button", { type: "button", className: kind === "highlights" ? "is-on" : "", onClick: () => onKind("highlights"), children: "Highlights" }),
        ],
      }),
      jsxs("label", {
        className: "sr-field",
        children: [
          jsx("span", { children: "Caption style" }),
          jsx("select", {
            value: style,
            onChange: (event) => onStyle(event.target.value),
            children: options.map((item) => jsx("option", { value: item.id, children: item.id }, item.id)),
          }),
        ],
      }),
      jsx("p", { className: "sr-hint", children: (options.find((item) => item.id === style) || {}).description || "" }),
      jsx("button", {
        type: "submit",
        className: "sr-primary",
        disabled: blocked || busy === "start" || !String(source || "").trim(),
        children: busy === "start" ? "Starting…" : "Select moments",
      }),
      jsx("p", { className: "sr-rights", children: "Only clip video you have the rights to." }),
    ],
  });
}

export function RunList({ runs, activeId, onOpen, onNew }) {
  return jsxs("aside", {
    className: "sr-rail",
    "data-testid": "run-list",
    children: [
      jsxs("div", {
        className: "sr-brand",
        children: [
          jsx("span", { className: "sr-mark", "aria-hidden": "true" }),
          jsxs("div", {
            children: [
              jsx("strong", { children: "Shortrizz" }),
              jsx("em", { children: "Studio" }),
            ],
          }),
        ],
      }),
      jsx("button", { type: "button", className: "sr-new", onClick: onNew, children: "New run" }),
      jsx("div", {
        className: "sr-runs",
        children: runs.length
          ? runs.map((run) => jsxs("button", {
            type: "button",
            className: cx("sr-run", run.id === activeId && "is-on"),
            onClick: () => onOpen(run.id),
            children: [
              jsx("span", { className: "sr-run-title", children: run.title || run.id }),
              jsxs("span", {
                className: "sr-run-meta",
                children: [
                  jsx("span", { className: cx("sr-pill", "is-" + run.stage), children: STAGE_LABEL[run.stage] || run.stage }),
                  run.kind ? jsx("span", { className: "sr-kind", children: run.kind }) : null,
                ],
              }),
              jsx(ProgressMeter, { progress: run.progress, stage: run.stage }),
            ],
          }, run.id))
          : jsx("p", { className: "sr-empty-rail", children: "No runs yet. Start a select-only pass." }),
      }),
    ],
  });
}

export function ProgressMeter({ progress, stage }) {
  const total = Number(progress && progress.total) || 0;
  const done = Number(progress && progress.done) || 0;
  const pct = stage === "done" || stage === "picks_ready" ? 100 : (total ? Math.max(6, Math.round((done / total) * 100)) : (stage === "selecting" || stage === "rendering" ? 18 : 0));
  return jsx("span", {
    className: "sr-meter",
    "aria-hidden": "true",
    children: jsx("span", { style: { width: pct + "%" } }),
  });
}

function CaptionOverlay({ pick, at, wide }) {
  const line = captionLine(pick, at);
  return jsx("div", {
    className: cx("sr-caption", line.className, wide && "is-lower"),
    "data-testid": "caption-overlay",
    children: line.tokens.map((token, index) => jsx("span", {
      className: token.on ? "sr-cap-on" : "sr-cap-off",
      children: token.text,
    }, index)),
  });
}

export function PickCard({ pick, wide, media, playing, pinned, loading, playhead, onChange, onNudge, onTime, onPreview, onPin, onStop, onDropPick, onPlayhead }) {
  const still = mediaUrl(media, pick.still);
  const video = mediaUrl(media, previewKey(pick));
  const showVideo = playing && video;
  const at = playing && playhead != null ? playhead : null;
  return jsxs("article", {
    className: cx("sr-card", !pick.enabled && "is-off", playing && "is-playing"),
    "data-testid": "pick-card",
    draggable: true,
    onDragStart: (event) => {
      if (event.dataTransfer) event.dataTransfer.setData("text/plain", pick.id);
      event.dataTransfer && event.dataTransfer.setData("application/x-shortrizz-pick", pick.id);
    },
    onDragOver: (event) => event.preventDefault(),
    onDrop: (event) => {
      event.preventDefault();
      const from = (event.dataTransfer && (event.dataTransfer.getData("text/plain") || event.dataTransfer.getData("application/x-shortrizz-pick"))) || "";
      if (from) onDropPick(from, pick.id);
    },
    children: [
      jsxs("div", {
        className: cx("sr-frame", wide && "is-wide"),
        onMouseEnter: () => onPreview(pick),
        onMouseLeave: () => { if (!pinned) onStop(); },
        onClick: () => onPin(pick),
        children: [
          showVideo
            ? jsx("video", {
              src: video,
              poster: still || undefined,
              autoPlay: true,
              muted: !pinned,
              playsInline: true,
              loop: true,
              onTimeUpdate: (event) => onPlayhead(Number(pick.start) + event.currentTarget.currentTime),
            })
            : (still ? jsx("img", { src: still, alt: "" }) : jsx("div", { className: "sr-frame-fallback", children: "No still" })),
          jsx(CaptionOverlay, { pick, at, wide }),
          jsx("span", { className: "sr-score", children: String(pick.score ?? "") }),
          loading ? jsx("span", { className: "sr-loading", children: "Preview…" }) : null,
          !showVideo ? jsx("span", { className: "sr-play", children: "Play" }) : null,
        ],
      }),
      jsxs("div", {
        className: "sr-card-body",
        children: [
          jsx("input", {
            className: "sr-title",
            "aria-label": "Title",
            value: pick.title,
            onChange: (event) => onChange(pick.id, { title: event.target.value }),
          }),
          pick.hook ? jsx("p", { className: "sr-hook", children: pick.hook }) : null,
          pick.transcript ? jsx("p", { className: "sr-snip", children: pick.transcript }) : null,
          jsxs("p", {
            className: "sr-when",
            children: [
              fmtClock(pick.start),
              " – ",
              fmtClock(pick.end),
              jsx("span", { children: fmtDuration(pick.start, pick.end) }),
            ],
          }),
          jsxs("div", {
            className: "sr-trim",
            children: [
              jsx(TrimField, { label: "In", value: pick.start, onNudge: (delta) => onNudge(pick, "start", delta), onTime: (value, snap) => onTime(pick.id, "start", value, snap) }),
              jsx(TrimField, { label: "Out", value: pick.end, onNudge: (delta) => onNudge(pick, "end", delta), onTime: (value, snap) => onTime(pick.id, "end", value, snap) }),
            ],
          }),
          jsxs("div", {
            className: "sr-card-row",
            children: [
              jsx("select", {
                "aria-label": "Caption style",
                value: STYLE_META_IDS.includes(pick.caption_style) ? pick.caption_style : "hormozi",
                onChange: (event) => onChange(pick.id, { caption_style: event.target.value }),
                children: STYLE_META_IDS.map((id) => jsx("option", { value: id, children: id }, id)),
              }),
              jsx("button", {
                type: "button",
                className: cx("sr-toggle", pick.enabled && "is-on"),
                "aria-pressed": pick.enabled ? "true" : "false",
                onClick: () => onChange(pick.id, { enabled: !pick.enabled }),
                children: pick.enabled ? "On" : "Off",
              }),
            ],
          }),
        ],
      }),
    ],
  });
}

const STYLE_META_IDS = ["hormozi", "mrbeast", "abdaal"];

function TrimField({ label, value, onNudge, onTime }) {
  return jsxs("label", {
    className: "sr-trim-field",
    children: [
      jsx("span", { children: label }),
      jsxs("span", {
        className: "sr-nudge",
        children: [
          jsx("button", { type: "button", "aria-label": label + " minus half a second", onClick: () => onNudge(-0.5), children: "−0.5" }),
          jsx("input", {
            "aria-label": label + " seconds",
            inputMode: "decimal",
            value: String(value),
            onChange: (event) => onTime(event.target.value, false),
            onBlur: (event) => onTime(event.target.value, true),
          }),
          jsx("button", { type: "button", "aria-label": label + " plus half a second", onClick: () => onNudge(0.5), children: "+0.5" }),
        ],
      }),
    ],
  });
}

export function Storyboard({ picks, wide, media, playing, pinned, loadingId, playhead, onChange, onNudge, onTime, onPreview, onPin, onStop, onDropPick, onPlayhead }) {
  return jsx("div", {
    className: "sr-board",
    "data-testid": "storyboard",
    children: picks.map((pick) => jsx(PickCard, {
      pick,
      wide,
      media,
      playing: playing === pick.id,
      pinned: pinned === pick.id,
      loading: loadingId === pick.id,
      playhead: playing === pick.id ? playhead : null,
      onChange,
      onNudge,
      onTime,
      onPreview,
      onPin,
      onStop,
      onDropPick,
      onPlayhead,
    }, pick.id)),
  });
}

export function RenderStrip({ renders, selectedId, restyleStyle, busy, onSelect, onRestyleStyle, onRestyle, onOpen, media }) {
  const selected = renders.find((row) => row.id === selectedId) || [...renders].reverse().find((row) => row.status === "done") || renders[renders.length - 1];
  if (!renders.length) return null;
  return jsxs("section", {
    className: "sr-renders",
    "data-testid": "renders",
    children: [
      jsxs("div", {
        className: "sr-render-bar",
        children: [
          jsx("h3", { children: "Renders" }),
          jsx("select", {
            "aria-label": "Render version",
            value: selected ? selected.id : "",
            onChange: (event) => onSelect(event.target.value),
            children: renders.map((row) => jsx("option", {
              value: row.id,
              children: row.id + " · " + row.status + (row.style ? " · " + row.style : ""),
            }, row.id)),
          }),
          jsx("select", {
            "aria-label": "Restyle as",
            value: restyleStyle,
            onChange: (event) => onRestyleStyle(event.target.value),
            children: STYLE_META_IDS.map((id) => jsx("option", { value: id, children: id }, id)),
          }),
          jsx("button", { type: "button", onClick: onRestyle, disabled: busy === "restyle", children: busy === "restyle" ? "Restyling…" : "Restyle" }),
          jsx("button", { type: "button", onClick: onOpen, children: "Open folder" }),
        ],
      }),
      selected && selected.status === "rendering"
        ? jsx("p", { className: "sr-progress-label", children: progressLabel("rendering", selected.progress) })
        : null,
      selected && selected.clips && selected.clips.length
        ? jsx("div", {
          className: "sr-players",
          children: selected.clips.map((clip) => {
            const url = mediaUrl(media, clip.path);
            const image = typeof url === "string" && (url.startsWith("data:image") || /\.(png|jpe?g|webp)(\?|$)/i.test(url));
            return jsxs("figure", {
              className: "sr-player",
              children: [
                image
                  ? jsx("img", { src: url, alt: "" })
                  : (url
                    ? jsx("video", { src: url, controls: true, playsInline: true, preload: "metadata" })
                    : jsx("div", { className: "sr-player-wait", children: "Loading…" })),
                jsxs("figcaption", { children: [clip.title || "Clip", clip.duration != null ? " · " + Number(clip.duration).toFixed(1) + "s" : ""] }),
              ],
            }, clip.path);
          }),
        })
        : null,
    ],
  });
}

export function StudioHeader({ detail, dirty, busy, versions, confirmRepick, onRestore, onSave, onRepick, onRender }) {
  const stage = detail.stage || "picks_ready";
  return jsxs("header", {
    className: "sr-top",
    children: [
      jsxs("div", {
        className: "sr-top-copy",
        children: [
          jsx("h2", { children: detail.title || detail.id }),
          jsxs("p", {
            children: [
              jsx("span", { className: cx("sr-pill", "is-" + stage), children: STAGE_LABEL[stage] || stage }),
              detail.kind ? jsx("span", { className: "sr-kind", children: detail.kind }) : null,
              jsx("span", { children: progressLabel(stage, detail.progress) }),
            ],
          }),
          stage === "rendering" || stage === "selecting" ? jsx(ProgressMeter, { progress: detail.progress, stage }) : null,
        ],
      }),
      jsxs("div", {
        className: "sr-actions",
        children: [
          jsx("select", {
            "aria-label": "Picks version",
            value: detail.revision == null ? "" : String(detail.revision),
            onChange: (event) => onRestore(Number(event.target.value)),
            children: (versions || []).map((row) => jsx("option", {
              value: String(row.revision),
              children: "v" + row.revision,
            }, row.revision)),
          }),
          jsx("button", { type: "button", onClick: () => onRestore(Math.max(1, Number(detail.revision || 1) - 1)), disabled: !detail.revision || detail.revision <= 1, children: "Undo" }),
          jsx("button", { type: "button", onClick: onSave, disabled: !dirty || busy === "save", children: busy === "save" ? "Saving…" : (dirty ? "Save" : "Saved") }),
          jsx("button", { type: "button", className: confirmRepick ? "is-danger" : "", onClick: onRepick, children: confirmRepick ? "Re-pick now" : "Re-pick" }),
          jsx("button", {
            type: "button",
            className: "sr-primary",
            onClick: onRender,
            disabled: busy === "render" || stage === "selecting",
            children: busy === "render" ? "Rendering…" : (dirty ? "Save & render" : "Render approved"),
          }),
        ],
      }),
    ],
  });
}
