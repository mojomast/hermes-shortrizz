import assert from "node:assert/strict";
import test from "node:test";

import { StudioPane } from "../../desktop/src/ui/pane.js";
import { createPaneContribution, registerShortrizzPane } from "../../desktop/src/ui/register.js";
import { resetFiber, rerender } from "./shims/jsx-runtime.mjs";

function walk(node, visit) {
  if (node == null || node === false || node === true) return;
  if (Array.isArray(node)) {
    node.forEach((child) => walk(child, visit));
    return;
  }
  if (typeof node === "string" || typeof node === "number") {
    visit({ text: String(node) });
    return;
  }
  visit(node);
  if (node.props) walk(node.props.children, visit);
}

function texts(node) {
  const out = [];
  walk(node, (item) => {
    if (item.text) out.push(item.text);
  });
  return out.join(" ");
}

function findAll(node, pred) {
  const out = [];
  walk(node, (item) => {
    if (item.props && pred(item)) out.push(item);
  });
  return out;
}

function byTestId(node, id) {
  return findAll(node, (item) => item.props["data-testid"] === id);
}

const still = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E";

function storyRun() {
  return {
    id: "run-1",
    stage: "picks_ready",
    title: "Talk",
    kind: "shorts",
    progress: { done: 2, total: 2 },
    revision: 1,
    versions: [{ revision: 1 }],
    error: "",
    renders: [{
      id: "v1", status: "done", style: "hormozi", kind: "render",
      progress: { done: 1, total: 1 },
      clips: [{ title: "Hello", duration: 1.2, path: "renders/v1/01.mp4" }],
    }],
    picks: [
      {
        id: "p01", order: 1, enabled: true, start: 1, end: 2.3, score: 88,
        title: "Hello", hook: "Opens on a claim", transcript: "hello world",
        caption_style: "hormozi", still: "stills/p01.jpg",
        words: [{ w: "hello", s: 1, e: 1.4 }, { w: "world", s: 1.5, e: 2 }],
      },
      {
        id: "p02", order: 2, enabled: true, start: 5, end: 6.2, score: 70,
        title: "Again", hook: "Pays off", transcript: "again friend",
        caption_style: "abdaal", still: "stills/p02.jpg",
        words: [{ w: "again", s: 5, e: 5.4 }, { w: "friend", s: 5.5, e: 6 }],
      },
    ],
  };
}

test("register wires a wide Studio pane through ctx.rest", () => {
  resetFiber();
  const seen = [];
  const ctx = {
    register(contribution) { seen.push(contribution); },
    rest(path) { return Promise.resolve({ ok: true, path }); },
    os: { revealPath(path) { return Promise.resolve(path); } },
    setInterval() { return () => {}; },
  };
  assert.equal(registerShortrizzPane(ctx), true);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].title, "Shortrizz Studio");
  assert.equal(seen[0].area, "panes");
  assert.equal(seen[0].data.width, "960px");
  assert.equal(seen[0].data.placement, "right");
  const contribution = createPaneContribution();
  resetFiber();
  const tree = contribution.render();
  assert.match(texts(tree), /Pick the moments first/);
  assert.equal(registerShortrizzPane(null), false);
});

test("storyboard edits, caption overlay, reorder, and save", async () => {
  resetFiber();
  const calls = [];
  const run = storyRun();
  const rest = (path, opts = {}) => {
    calls.push({ path, body: opts.body, method: opts.method });
    if (path.startsWith("/runs/run-1/picks")) {
      const next = { ...run, revision: 2, versions: [{ revision: 1 }, { revision: 2 }], picks: opts.body.picks };
      return Promise.resolve({ ok: true, run: next });
    }
    if (path === "/runs/run-1/open") return Promise.resolve({ ok: true, path: "/runs/run-1" });
    if (path === "/runs/run-1/preview") return Promise.resolve({ ok: true, path: "previews/p01_1000_2300.mp4" });
    if (path.startsWith("/media")) return Promise.resolve({ ok: true, mime: "video/mp4", data_b64: "" });
    return Promise.resolve({ ok: false, error: "unexpected " + path });
  };
  const revealed = [];
  const props = {
    transport: { rest, reveal: (path) => revealed.push(path) },
    initial: {
      skipLoad: true,
      runId: "run-1",
      detail: run,
      health: {
        deps: {
          ffmpeg: true, ffprobe: true, yt_dlp: true,
          opencode_go_api_key: { name: "OPENCODE_GO_API_KEY", set: false },
        },
        styles: [{ id: "hormozi", description: "Bold" }],
      },
      runs: [{ id: "run-1", title: "Talk", stage: "picks_ready", kind: "shorts", progress: { done: 2, total: 2 } }],
      media: { "stills/p01.jpg": still, "stills/p02.jpg": still, "renders/v1/01.mp4": still },
    },
  };
  let tree = rerender(StudioPane, props);
  const text = texts(tree);
  assert.match(text, /Talk/);
  assert.match(text, /Hello/);
  assert.match(text, /HELLO/);
  assert.match(text, /OPENCODE_GO_API_KEY is not set/);
  assert.equal(text.includes("sk-"), false);
  assert.equal(byTestId(tree, "storyboard").length, 1);
  assert.equal(byTestId(tree, "pick-card").length, 2);
  const image = findAll(tree, (item) => item.type === "img")[0];
  assert.equal(image.props.src, still);

  const nudge = findAll(tree, (item) => item.props["aria-label"] === "In plus half a second")[0];
  nudge.props.onClick();
  tree = rerender(StudioPane, props);
  const inputs = findAll(tree, (item) => item.props["aria-label"] === "In seconds");
  assert.equal(inputs[0].props.value, "1.5");

  const cards = byTestId(tree, "pick-card");
  cards[1].props.onDrop({
    preventDefault() {},
    dataTransfer: { getData() { return "p01"; } },
  });
  tree = rerender(StudioPane, props);
  const titles = findAll(tree, (item) => item.props["aria-label"] === "Title").map((item) => item.props.value);
  assert.deepEqual(titles, ["Again", "Hello"]);

  const save = findAll(tree, (item) => item.type === "button" && item.props.children === "Save")[0];
  assert.equal(save.props.disabled, false);
  await save.props.onClick();
  tree = rerender(StudioPane, props);
  assert.equal(calls.some((call) => call.path === "/runs/run-1/picks" && call.body.picks[0].id === "p02"), true);
  assert.match(texts(tree), /Saved picks v2/);

  const open = findAll(tree, (item) => item.type === "button" && item.props.children === "Open folder")[0];
  await open.props.onClick();
  assert.deepEqual(revealed, ["/runs/run-1"]);
});

test("onboarding posts a select-only highlights run", async () => {
  resetFiber();
  const calls = [];
  const rest = (path, opts = {}) => {
    calls.push({ path, body: opts.body });
    if (path === "/runs" && opts.body) {
      return Promise.resolve({ ok: true, run_id: "run-new", stage: "selecting" });
    }
    if (path === "/runs") return Promise.resolve({ ok: true, runs: [] });
    if (path === "/runs/run-new") {
      return Promise.resolve({
        ok: true,
        run: { id: "run-new", stage: "selecting", title: "Queued", kind: "shorts", progress: { done: 0, total: 0 }, picks: [], versions: [], renders: [] },
      });
    }
    if (path === "/health") {
      return Promise.resolve({
        ok: true,
        deps: { ffmpeg: false, ffprobe: false, yt_dlp: false, opencode_go_api_key: { name: "OPENCODE_GO_API_KEY", set: false } },
        styles: [{ id: "hormozi", description: "Bold" }],
      });
    }
    return Promise.resolve({ ok: false, error: "no" });
  };
  const props = { transport: { rest }, initial: { skipLoad: true, health: {
    deps: { ffmpeg: true, ffprobe: true, yt_dlp: true, opencode_go_api_key: { name: "OPENCODE_GO_API_KEY", set: true } },
    styles: [{ id: "hormozi", description: "Bold uppercase." }, { id: "mrbeast", description: "Chunky." }],
  } } };
  let tree = rerender(StudioPane, props);
  assert.equal(byTestId(tree, "onboarding").length, 1);
  const input = findAll(tree, (item) => item.type === "input")[0];
  input.props.onChange({ target: { value: "https://example.com/watch" } });
  tree = rerender(StudioPane, props);
  const highlights = findAll(tree, (item) => item.type === "button" && item.props.children === "Highlights")[0];
  highlights.props.onClick();
  tree = rerender(StudioPane, props);
  const form = byTestId(tree, "onboarding")[0];
  await form.props.onSubmit({ preventDefault() {} });
  assert.equal(calls[0].path, "/runs");
  assert.equal(calls[0].body.kind, "highlights");
  assert.equal(calls[0].body.stage, undefined);
  assert.equal(calls[0].body.source, "https://example.com/watch");
});
