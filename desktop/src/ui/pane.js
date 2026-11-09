import { useEffect, useRef, useState } from "react";
import { jsx, jsxs } from "react/jsx-runtime";
import { blobUrlFromBase64, createApi, errorText } from "./api.js";
import { getHostTransport } from "./host.js";
import {
  clonePick,
  depNotes,
  mediaUrl,
  nudgeEdge,
  picksFingerprint,
  previewKey,
  reorderPicks,
  round3,
  snapEdge,
} from "./logic.js";
import { DepNotes, Onboarding, RenderStrip, RunList, Storyboard, StudioHeader } from "./views.js";

function applyRun(body, setDetail, setDraft, replaceDraft) {
  const run = body.run;
  setDetail(run);
  if (replaceDraft) setDraft((run.picks || []).map(clonePick));
  return run;
}

export function StudioPane({ transport, initial }) {
  const host = transport || getHostTransport() || {};
  const skip = !!(initial && initial.skipLoad);
  const [health, setHealth] = useState((initial && initial.health) || null);
  const [runs, setRuns] = useState((initial && initial.runs) || []);
  const [runId, setRunId] = useState((initial && initial.runId) || "");
  const [detail, setDetail] = useState((initial && initial.detail) || null);
  const [draft, setDraft] = useState(
    initial && initial.detail && initial.detail.picks ? initial.detail.picks.map(clonePick) : []
  );
  const [error, setError] = useState((initial && initial.error) || "");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState("");
  const [playing, setPlaying] = useState((initial && initial.playing) || "");
  const [pinned, setPinned] = useState((initial && initial.pinned) || "");
  const [playhead, setPlayhead] = useState(initial && initial.playhead != null ? initial.playhead : null);
  const [media, setMedia] = useState((initial && initial.media) || {});
  const [loadingId, setLoadingId] = useState("");
  const [source, setSource] = useState((initial && initial.source) || "");
  const [kind, setKind] = useState((initial && initial.kind) || "shorts");
  const [style, setStyle] = useState((initial && initial.style) || "hormozi");
  const [restyleStyle, setRestyleStyle] = useState("mrbeast");
  const [confirmRepick, setConfirmRepick] = useState(false);
  const [renderId, setRenderId] = useState((initial && initial.renderId) || "");
  const [composing, setComposing] = useState(!(initial && initial.runId));

  const restRef = useRef(host.rest || null);
  restRef.current = host.rest || null;
  const runRef = useRef(runId);
  runRef.current = runId;
  const mediaRef = useRef(media);
  mediaRef.current = media;

  const dirty = !!(detail && picksFingerprint(draft) !== picksFingerprint(detail.picks || []));
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  function api() {
    return createApi(restRef.current);
  }

  useEffect(() => {
    if (skip) return undefined;
    let stop = false;
    api().health().then((body) => { if (!stop) setHealth(body); }).catch((err) => { if (!stop) setError(errorText(err)); });
    api().runs().then((body) => { if (!stop) setRuns(body.runs || []); }).catch(() => {});
    return () => { stop = true; };
  }, [skip]);

  useEffect(() => {
    if (skip || typeof host.setInterval !== "function") return undefined;
    return host.setInterval(() => {
      const id = runRef.current;
      api().runs().then((body) => setRuns(body.runs || [])).catch(() => {});
      if (!id || dirtyRef.current) return;
      api().run(id).then((body) => {
        if (runRef.current !== id || dirtyRef.current) return;
        applyRun(body, setDetail, setDraft, true);
      }).catch(() => {});
    }, 2500);
  }, [skip, host.setInterval]);

  useEffect(() => {
    if (!detail || !restRef.current) return undefined;
    let stop = false;
    const client = api();
    const rels = [];
    (detail.picks || []).forEach((pick) => { if (pick.still) rels.push(pick.still); });
    (detail.renders || []).forEach((row) => {
      (row.clips || []).forEach((clip) => { if (clip.path) rels.push(clip.path); });
    });
    rels.forEach((rel) => {
      if (mediaRef.current[rel]) return;
      mediaRef.current = { ...mediaRef.current, [rel]: "pending" };
      client.media(detail.id, rel).then((body) => {
        if (stop) return;
        const url = blobUrlFromBase64(body.data_b64, body.mime);
        if (!url) return;
        setMedia((map) => ({ ...map, [rel]: url }));
      }).catch(() => {
        mediaRef.current = { ...mediaRef.current, [rel]: "" };
      });
    });
    return () => { stop = true; };
  }, [detail && detail.id, detail && detail.revision, detail && detail.stage, (detail && detail.renders || []).length]);

  function openRun(id) {
    setComposing(false);
    setRunId(id);
    setError("");
    setInfo("");
    setPlaying("");
    setPinned("");
    setConfirmRepick(false);
    setBusy("load");
    return api().run(id).then((body) => {
      applyRun(body, setDetail, setDraft, true);
      setBusy("");
    }).catch((err) => {
      setBusy("");
      setError(errorText(err));
    });
  }

  function onNew() {
    setComposing(true);
    setRunId("");
    setDetail(null);
    setDraft([]);
    setError("");
    setInfo("");
    setPlaying("");
    setPinned("");
  }

  function onStart() {
    const value = String(source || "").trim();
    if (!value) return Promise.resolve();
    setBusy("start");
    setError("");
    return api().create({ source: value, kind, style }).then((body) => {
      setBusy("");
      setComposing(false);
      setRunId(body.run_id);
      return api().runs().then((listed) => {
        setRuns(listed.runs || []);
        return openRun(body.run_id);
      });
    }).catch((err) => {
      setBusy("");
      setError(errorText(err));
    });
  }

  function onChange(id, patch) {
    setDraft((rows) => rows.map((pick) => (pick.id === id ? { ...pick, ...patch } : pick)));
  }

  function onNudge(pick, edge, delta) {
    const words = pick.words || [];
    const next = nudgeEdge(words, pick[edge], edge, delta);
    const other = edge === "start" ? "end" : "start";
    if (edge === "start" && next >= Number(pick.end)) return;
    if (edge === "end" && next <= Number(pick.start)) return;
    onChange(pick.id, { [edge]: next, [other]: pick[other] });
  }

  function onTime(id, edge, value, snap) {
    setDraft((rows) => rows.map((pick) => {
      if (pick.id !== id) return pick;
      const n = Number(value);
      if (!Number.isFinite(n)) return pick;
      const next = snap ? snapEdge(pick.words || [], n, edge, 0.45) : round3(n);
      return { ...pick, [edge]: next };
    }));
  }

  function onDropPick(fromId, toId) {
    setDraft((rows) => reorderPicks(rows, fromId, toId));
  }

  function onPreview(pick) {
    setPlaying(pick.id);
    setPlayhead(Number(pick.start));
    const key = previewKey(pick);
    if (mediaUrl(media, key) || mediaRef.current[key] === "pending") return Promise.resolve();
    mediaRef.current = { ...mediaRef.current, [key]: "pending" };
    setLoadingId(pick.id);
    return api().preview(runId, pick.id).then((body) => api().media(runId, body.path)).then((file) => {
      const url = blobUrlFromBase64(file.data_b64, file.mime);
      setMedia((map) => ({ ...map, [key]: url }));
      setLoadingId("");
    }).catch((err) => {
      setLoadingId("");
      mediaRef.current = { ...mediaRef.current, [key]: "" };
      setInfo(errorText(err));
    });
  }

  function onPin(pick) {
    setPinned(pick.id);
    return onPreview(pick);
  }

  function onSave() {
    if (!detail) return Promise.resolve();
    setBusy("save");
    setError("");
    return api().save(detail.id, { picks: draft, snap: true }).then((body) => {
      applyRun(body, setDetail, setDraft, true);
      setBusy("");
      setInfo("Saved picks v" + body.run.revision + ".");
    }).catch((err) => {
      setBusy("");
      setError(errorText(err));
    });
  }

  function onRestore(revision) {
    if (!detail || !revision || revision === detail.revision) return Promise.resolve();
    setBusy("save");
    return api().restore(detail.id, revision).then((body) => {
      applyRun(body, setDetail, setDraft, true);
      setBusy("");
      setInfo("Restored as v" + body.run.revision + ".");
    }).catch((err) => {
      setBusy("");
      setError(errorText(err));
    });
  }

  function onRepick() {
    if (!confirmRepick) {
      setConfirmRepick(true);
      return Promise.resolve();
    }
    setConfirmRepick(false);
    setBusy("repick");
    return api().repick(detail.id).then((body) => {
      setBusy("");
      return api().runs().then((listed) => {
        setRuns(listed.runs || []);
        return openRun(body.run_id);
      });
    }).catch((err) => {
      setBusy("");
      setError(errorText(err));
    });
  }

  function onRender() {
    if (!detail) return Promise.resolve();
    setBusy("render");
    setError("");
    const payload = dirty ? draft : null;
    return api().render(detail.id, payload).then(() => api().run(detail.id)).then((body) => {
      applyRun(body, setDetail, setDraft, true);
      setBusy("");
      setInfo("Rendering the approved picks.");
    }).catch((err) => {
      setBusy("");
      setError(errorText(err));
    });
  }

  function onRestyle() {
    if (!detail) return Promise.resolve();
    setBusy("restyle");
    return api().restyle(detail.id, restyleStyle).then(() => api().run(detail.id)).then((body) => {
      applyRun(body, setDetail, setDraft, false);
      setBusy("");
    }).catch((err) => {
      setBusy("");
      setError(errorText(err));
    });
  }

  function onOpen() {
    if (!detail) return Promise.resolve();
    return api().open(detail.id).then((body) => {
      if (host.reveal) return host.reveal(body.path);
      setInfo("Run folder: " + body.path);
      return body;
    }).catch((err) => setError(errorText(err)));
  }

  const notes = depNotes(health && health.deps);
  const blocked = !!(health && health.deps && (!health.deps.ffmpeg || !health.deps.ffprobe));
  const showBoard = detail && !composing;
  const wide = detail && detail.kind === "highlights";

  return jsxs("div", {
    className: "sr-studio",
    "data-testid": "studio-root",
    children: [
      jsx(RunList, { runs, activeId: composing ? "" : runId, onOpen: openRun, onNew }),
      jsxs("section", {
        className: "sr-main",
        children: [
          jsx(DepNotes, { notes }),
          error ? jsx("p", { className: "sr-banner is-bad", role: "alert", children: error }) : null,
          info ? jsx("p", { className: "sr-banner is-info", children: info }) : null,
          detail && detail.error && detail.stage === "error"
            ? jsx("p", { className: "sr-banner is-bad", role: "alert", children: detail.error })
            : null,
          showBoard
            ? jsxs("div", {
              className: "sr-workspace",
              children: [
                jsx(StudioHeader, {
                  detail,
                  dirty,
                  busy,
                  versions: detail.versions || [],
                  confirmRepick,
                  onRestore,
                  onSave,
                  onRepick,
                  onRender,
                }),
                draft.length
                  ? jsx(Storyboard, {
                    picks: draft,
                    wide,
                    media,
                    playing,
                    pinned,
                    loadingId,
                    playhead,
                    onChange,
                    onNudge,
                    onTime,
                    onPreview,
                    onPin,
                    onStop: () => setPlaying(""),
                    onDropPick,
                    onPlayhead: setPlayhead,
                  })
                  : jsx("p", { className: "sr-lead", children: busy === "load" ? "Loading picks…" : "No picks in this run yet." }),
                jsx(RenderStrip, {
                  renders: detail.renders || [],
                  selectedId: renderId,
                  restyleStyle,
                  busy,
                  onSelect: setRenderId,
                  onRestyleStyle: setRestyleStyle,
                  onRestyle,
                  onOpen,
                  media,
                }),
              ],
            })
            : jsx(Onboarding, {
              source,
              kind,
              style,
              styles: health && health.styles,
              busy,
              blocked,
              onSource: setSource,
              onKind: setKind,
              onStyle: setStyle,
              onStart,
            }),
        ],
      }),
    ],
  });
}
