import assert from "node:assert/strict";
import test from "node:test";

import {
  captionLine,
  depNotes,
  nudgeEdge,
  picksFingerprint,
  reorderPicks,
  snapEdge,
} from "../../desktop/src/ui/logic.js";

const words = [
  { w: "hello", s: 1, e: 1.4 },
  { w: "world", s: 1.5, e: 2 },
  { w: "again", s: 5, e: 5.4 },
];

test("nudge snaps to a word boundary", () => {
  assert.equal(snapEdge(words, 1.2, "start"), 1);
  assert.equal(snapEdge(words, 9, "end"), 9);
  assert.equal(nudgeEdge(words, 1, "start", 0.5), 1.5);
  assert.equal(nudgeEdge(words, 1.5, "end", 0.5), 2);
});

test("reorder keeps a dense order", () => {
  const next = reorderPicks(
    [{ id: "p01", order: 1 }, { id: "p02", order: 2 }, { id: "p03", order: 3 }],
    "p01",
    "p03"
  );
  assert.deepEqual(next.map((pick) => pick.id), ["p02", "p03", "p01"]);
  assert.deepEqual(next.map((pick) => pick.order), [1, 2, 3]);
});

test("caption overlay approximates the ASS style", () => {
  const line = captionLine({
    start: 0.8,
    end: 2.3,
    caption_style: "hormozi",
    words,
    transcript: "hello world",
  }, 1.6);
  assert.equal(line.className, "sr-cap-hormozi");
  assert.deepEqual(line.tokens.map((token) => token.text), ["HELLO", "WORLD"]);
  assert.equal(line.tokens.find((token) => token.on).text, "WORLD");
  const quiet = captionLine({
    start: 0,
    end: 3,
    caption_style: "abdaal",
    words: [],
    transcript: "a quiet line here",
  }, null);
  assert.equal(quiet.className, "sr-cap-abdaal");
  assert.equal(quiet.tokens[0].text, "a");
});

test("dependency notes name the key and never a value", () => {
  const notes = depNotes({
    ffmpeg: false,
    ffprobe: false,
    yt_dlp: false,
    opencode_go_api_key: { name: "OPENCODE_GO_API_KEY", set: false },
  });
  const text = notes.map((note) => note.text).join(" ");
  assert.match(text, /ffmpeg/);
  assert.match(text, /yt-dlp/);
  assert.match(text, /OPENCODE_GO_API_KEY/);
  assert.equal(text.includes("sk-"), false);
  assert.equal(picksFingerprint([{ id: "p01", order: 1, enabled: true, start: 1, end: 2, title: "A", caption_style: "hormozi" }])
    === picksFingerprint([{ id: "p01", order: 1, enabled: true, start: 1, end: 2, title: "B", caption_style: "hormozi" }]), false);
});
