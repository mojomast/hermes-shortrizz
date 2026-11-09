/**
 * Render Shortrizz Studio with the mock host and screenshot it.
 * Usage: node --import ./tests/js/register-mocks.mjs scripts/shoot_studio.mjs
 */
import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { StudioPane } from "../desktop/src/ui/pane.js";
import { resetFiber, rerender } from "../tests/js/shims/jsx-runtime.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const OUT = path.join(ROOT, "docs", "images");
const CSS = readFileSync(path.join(ROOT, "desktop", "src", "ui", "styles.css"), "utf8");

function svg(hue, label) {
  const body = `<svg xmlns="http://www.w3.org/2000/svg" width="360" height="640" viewBox="0 0 360 640">
    <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="${hue}"/>
      <stop offset="1" stop-color="#120d18"/>
    </linearGradient></defs>
    <rect width="360" height="640" fill="url(#g)"/>
    <circle cx="180" cy="250" r="72" fill="#f3e6d4"/>
    <rect x="128" y="330" width="104" height="150" rx="40" fill="#f3e6d4"/>
    <text x="180" y="560" text-anchor="middle" fill="#ffffff" font-family="sans-serif" font-size="22" font-weight="700">${label}</text>
  </svg>`;
  return "data:image/svg+xml," + encodeURIComponent(body);
}

function escapeText(value) {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function escapeAttr(value) {
  return escapeText(value).replace(/"/g, "&quot;");
}

function styleOf(value) {
  if (!value || typeof value !== "object") return "";
  return Object.entries(value).map(([key, item]) => {
    const name = key.replace(/[A-Z]/g, (letter) => "-" + letter.toLowerCase());
    return name + ":" + item;
  }).join(";");
}

const VOID = new Set(["img", "input", "br", "hr", "meta", "link"]);

function toHtml(node, parentValue) {
  if (node == null || node === false || node === true) return "";
  if (Array.isArray(node)) return node.map((child) => toHtml(child, parentValue)).join("");
  if (typeof node === "string" || typeof node === "number") return escapeText(node);
  if (typeof node.type === "function") return toHtml(node.type(node.props || {}), parentValue);
  const props = node.props || {};
  const attrs = [];
  for (const [key, value] of Object.entries(props)) {
    if (key === "children" || key === "key" || value == null || typeof value === "function") continue;
    if (key === "value" && node.type === "select") continue;
    if (key === "className") attrs.push(`class="${escapeAttr(value)}"`);
    else if (key === "style" && typeof value === "object") attrs.push(`style="${escapeAttr(styleOf(value))}"`);
    else if (typeof value === "boolean") { if (value) attrs.push(key); }
    else attrs.push(`${key}="${escapeAttr(value)}"`);
  }
  if (node.type === "option" && parentValue != null && String(props.value) === String(parentValue)) {
    attrs.push("selected");
  }
  const open = `<${node.type}${attrs.length ? " " + attrs.join(" ") : ""}>`;
  if (VOID.has(node.type)) return open.replace(/>$/, " />");
  const nextParent = node.type === "select" ? props.value : undefined;
  return open + toHtml(props.children, nextParent) + `</${node.type}>`;
}

function page(body) {
  return `<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
  :root {
    --ui-text-primary: #f4f1ea;
    --ui-text-secondary: #d9d3c7;
    --ui-text-tertiary: #a39b90;
    --ui-stroke-secondary: rgba(244, 241, 234, 0.14);
    --ui-accent: #e7a2ff;
  }
  html, body { margin: 0; height: 100%; background: #14120f; color: var(--ui-text-primary); }
  #root { height: 100vh; }
  ${CSS}
</style>
</head>
<body><div id="root">${body}</div></body>
</html>`;
}

function render(initial) {
  resetFiber();
  return page(toHtml(rerender(StudioPane, {
    transport: { rest: () => Promise.resolve({ ok: true }) },
    initial: { skipLoad: true, ...initial },
  })));
}

const words = [
  { w: "hello", s: 12.2, e: 12.6 },
  { w: "world", s: 12.7, e: 13.1 },
  { w: "this", s: 13.2, e: 13.5 },
  { w: "is", s: 42, e: 42.2 },
  { w: "the", s: 42.3, e: 42.5 },
  { w: "one", s: 42.6, e: 43 },
];

const picks = [
  {
    id: "p01", order: 1, enabled: true, start: 12.0, end: 41.5, score: 91,
    title: "The line that hooks", hook: "Starts on a claim the viewer has to answer.",
    transcript: "hello world this is the moment", caption_style: "hormozi",
    still: "stills/p01.jpg",
    words: words.slice(0, 3),
  },
  {
    id: "p02", order: 2, enabled: true, start: 41.8, end: 70.2, score: 84,
    title: "Payoff", hook: "The example lands and the thought finishes.",
    transcript: "this is the one", caption_style: "mrbeast",
    still: "stills/p02.jpg",
    words: words.slice(3),
  },
  {
    id: "p03", order: 3, enabled: false, start: 80, end: 104, score: 62,
    title: "Cut this one", hook: "True, but it repeats the payoff.",
    transcript: "and that's the quiet version", caption_style: "abdaal",
    still: "stills/p03.jpg", words: [],
  },
];

const health = {
  deps: {
    ffmpeg: true,
    ffprobe: true,
    yt_dlp: false,
    opencode_go_api_key: { name: "OPENCODE_GO_API_KEY", set: false },
  },
  styles: [
    { id: "hormozi", description: "Bold uppercase, yellow active word." },
    { id: "mrbeast", description: "Chunky type, green active word." },
    { id: "abdaal", description: "Sentence case, blue glow on the active word." },
  ],
};

const runs = [
  { id: "run-demo", title: "How we ship", stage: "picks_ready", kind: "shorts", progress: { done: 3, total: 3 } },
  { id: "run-older", title: "Interview, take two", stage: "done", kind: "highlights", progress: { done: 1, total: 1 } },
];

const detail = {
  id: "run-demo",
  stage: "picks_ready",
  title: "How we ship",
  kind: "shorts",
  progress: { done: 3, total: 3 },
  revision: 2,
  versions: [{ revision: 1 }, { revision: 2 }],
  source: { title: "How we ship", url: "https://example.com/watch", duration: 754 },
  picks,
  renders: [],
  error: "",
};

const a = svg("#6d28d9", "01");
const b = svg("#b45309", "02");
const c = svg("#0f766e", "03");
const media = { "stills/p01.jpg": a, "stills/p02.jpg": b, "stills/p03.jpg": c, "renders/v1/01.mp4": a, "renders/v1/02.mp4": b };

const shots = {
  "studio-onboarding.png": { size: "1280,800", html: render({
    health,
    runs: [],
    source: "https://www.youtube.com/watch?v=kAnU7i0ybZE",
    kind: "shorts",
    style: "hormozi",
  }) },
  "studio-storyboard.png": { size: "1280,800", html: render({
    health: { ...health, deps: { ...health.deps, yt_dlp: true, opencode_go_api_key: { name: "OPENCODE_GO_API_KEY", set: true } } },
    runs,
    runId: "run-demo",
    detail,
    media,
  }) },
  "studio-render.png": { size: "1280,1320", html: render({
    health: { ...health, deps: { ...health.deps, yt_dlp: true, opencode_go_api_key: { name: "OPENCODE_GO_API_KEY", set: true } } },
    runs: [{ ...runs[0], stage: "done" }, runs[1]],
    runId: "run-demo",
    detail: {
      ...detail,
      stage: "done",
      progress: { done: 2, total: 2 },
      renders: [{
        id: "v1",
        status: "done",
        style: "hormozi",
        kind: "render",
        progress: { done: 2, total: 2 },
        clips: [
          { title: "The line that hooks", duration: 29.5, path: "renders/v1/01.mp4" },
          { title: "Payoff", duration: 28.4, path: "renders/v1/02.mp4" },
        ],
      }],
    },
    media,
    renderId: "v1",
  }) },
};

mkdirSync(OUT, { recursive: true });
const chrome = process.env.CHROME || "google-chrome";
for (const [name, shot] of Object.entries(shots)) {
  const file = path.join("/tmp", name.replace(".png", ".html"));
  writeFileSync(file, shot.html);
  const dest = path.join(OUT, name);
  const result = spawnSync(chrome, [
    "--headless=new",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--hide-scrollbars",
    "--force-device-scale-factor=1",
    "--virtual-time-budget=2500",
    "--timeout=4000",
    `--window-size=${shot.size}`,
    `--screenshot=${dest}`,
    `file://${file}`,
  ], { encoding: "utf8", timeout: 20000 });
  if (result.status !== 0) {
    console.error(result.stderr || result.stdout);
    process.exit(1);
  }
  console.log("wrote", path.relative(ROOT, dest));
}
