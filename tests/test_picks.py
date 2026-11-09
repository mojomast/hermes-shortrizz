"""Select → picks.json → render, including CLI flags, tool stage, and status."""
import json
from pathlib import Path

from shortrizz import cli, hermes_tools as H, picks
from shortrizz import pipeline


def _words():
    return [
        {"w": "hello", "s": 1.0, "e": 1.4},
        {"w": "world", "s": 1.5, "e": 2.0},
        {"w": "again", "s": 5.0, "e": 5.4},
        {"w": "friend", "s": 5.5, "e": 6.0},
    ]


def _clip(start, end, title, score, wr, style=None):
    a, b = wr
    words = _words()
    row = {"start": start, "end": end, "title": title, "virality_score": score,
           "why_it_hooks": f"hook for {title}", "caption": "watch this", "hashtags": ["#t"],
           "method": "heuristic", "word_range": [a, b],
           "transcript": " ".join(w["w"] for w in words[a:b + 1])}
    if style:
        row["caption_style"] = style
    return row


def _jpeg(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"\xff\xd8" + b"j" * 120)


def test_save_keeps_history_and_skips_unchanged(tmp_path):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")
    doc = picks.build_document(
        run_id="run", kind="shorts", source=picks.source_block({"title": "T"}, src, 10),
        settings={"style": "hormozi"}, clips=[_clip(0.8, 2.3, "Hello", 80, (0, 1))])
    snap = picks.save_picks(tmp_path, doc, refresh_stills=False)
    assert snap.name == "picks.v1.json"
    assert json.loads((tmp_path / "picks.json").read_text())["revision"] == 1
    again = picks.save_picks(tmp_path, json.loads((tmp_path / "picks.json").read_text()), refresh_stills=False)
    assert again.name == "picks.v1.json"
    edited = json.loads((tmp_path / "picks.json").read_text())
    edited["picks"][0]["title"] = "Edited"
    snap2 = picks.save_picks(tmp_path, edited, refresh_stills=False)
    assert snap2.name == "picks.v2.json"
    assert json.loads(snap2.read_text())["picks"][0]["title"] == "Edited"
    # An in-place edit of picks.json still snapshots against the latest revision.
    current = json.loads((tmp_path / "picks.json").read_text())
    current["picks"][0]["enabled"] = False
    (tmp_path / "picks.json").write_text(json.dumps(current))
    snap3 = picks.save_picks(tmp_path, current, refresh_stills=False)
    assert snap3.name == "picks.v3.json"


def test_load_rejects_bad_documents(tmp_path):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")
    doc = picks.build_document(
        run_id="run", kind="shorts", source=picks.source_block({"title": "T"}, src, 10),
        settings={"style": "hormozi"}, clips=[_clip(0.8, 2.3, "Hello", 80, (0, 1)),
                                               _clip(4.8, 6.2, "Again", 70, (2, 3))])
    picks.save_picks(tmp_path, doc, refresh_stills=False)
    path = tmp_path / "picks.json"
    bad = json.loads(path.read_text())
    bad["version"] = 9
    path.write_text(json.dumps(bad))
    try:
        picks.load_picks(path)
        raise AssertionError("expected version error")
    except ValueError as e:
        assert "version" in str(e)
    bad["version"] = 1
    bad["picks"][0]["end"] = bad["picks"][0]["start"]
    path.write_text(json.dumps(bad))
    try:
        picks.load_picks(path)
        raise AssertionError("expected trim error")
    except ValueError as e:
        assert "end" in str(e)
    bad["picks"][0]["end"] = 2.3
    bad["picks"][1]["id"] = bad["picks"][0]["id"]
    path.write_text(json.dumps(bad))
    try:
        picks.load_picks(path)
        raise AssertionError("expected duplicate id")
    except ValueError as e:
        assert "duplicate" in str(e)


def test_word_range_survives_padding_and_shrinks_on_trim():
    words = _words()
    pick = {"word_range": [0, 1]}
    assert picks.word_range_for(words, pick, 0.8, 2.3) == [0, 1]
    assert picks.word_range_for(words, pick, 1.45, 2.3) == [1, 1]
    grown = picks.word_range_for(words, pick, 0.8, 6.2)
    assert grown == [0, 3]


def test_select_only_writes_picks_and_stills(monkeypatch, tmp_path):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"videobytes")
    words = _words()
    tr = {"segments": [{"text": "hello world again friend", "words": words}]}
    clip = _clip(0.8, 2.3, "Hello", 88, (0, 1))
    monkeypatch.setattr(pipeline.download, "fetch", lambda *a, **k: (src, {"title": "Talk", "source": "file://v"}))
    monkeypatch.setattr(pipeline.T, "transcribe", lambda *a, **k: tr)
    monkeypatch.setattr(pipeline, "ffprobe_duration", lambda p: 12.0)
    monkeypatch.setattr(pipeline.clipselect, "select", lambda *a, **k: [clip])

    def fake_still(_src, _t, out_jpg, width=360):
        _jpeg(out_jpg)

    monkeypatch.setattr(pipeline.picks, "still_frame", fake_still)
    out = tmp_path / "run"
    doc = pipeline.make(str(src), out, count=1, min_len=1, max_len=8, use_llm=False,
                        select_only=True, watermark=False)
    assert doc["stage"] == "picks_ready"
    assert not (out / "clips.json").exists()
    saved = json.loads((out / "picks.json").read_text())
    assert saved["version"] == 1 and saved["kind"] == "shorts" and saved["run_id"] == "run"
    assert (out / "picks.v1.json").is_file()
    pick = saved["picks"][0]
    assert pick["id"] == "p01" and pick["title"] == "Hello" and pick["score"] == 88
    assert pick["hook"] == "hook for Hello" and pick["enabled"] is True
    assert pick["still"] == "stills/p01.jpg"
    assert (out / "stills" / "p01.jpg").stat().st_size > 100
    progress = json.loads((out / "progress.json").read_text())
    assert progress["stage"] == "picks_ready"


def test_render_from_picks_honours_enabled_order_trim_title_style(monkeypatch, tmp_path):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"videobytes")
    run = tmp_path / "run"
    clips = [_clip(0.8, 2.3, "First", 90, (0, 1)), _clip(4.8, 6.2, "Second", 70, (2, 3), style="abdaal")]
    doc = picks.build_document(
        run_id="run", kind="shorts", source=picks.source_block({"title": "Talk"}, src, 12),
        settings={"style": "hormozi", "model": "small", "cache": str(tmp_path / "cache"), "reframe": "center",
                  "preset": "ultrafast", "min": 1, "max": 8, "count": 2, "llm": False,
                  "watermark": {"watermark": False}, "edits": {"enabled": True, "loudnorm": False}},
        clips=clips, words=_words())
    doc["picks"][0]["enabled"] = False
    doc["picks"][1]["order"] = 0
    doc["picks"][1]["start"] = 5.45
    doc["picks"][1]["title"] = "Trimmed"
    doc["picks"][1]["caption_style"] = "mrbeast"
    picks.save_picks(run, doc, refresh_stills=False)
    monkeypatch.setattr(pipeline.T, "transcribe", lambda *a, **k: {"segments": [{"text": "x", "words": _words()}]})
    monkeypatch.setattr(pipeline.picks, "still_frame", lambda *a, **k: _jpeg(a[2]))
    captured = {}

    def fake_render(src, words, clips, out, style_name, *a, **k):
        captured["clips"] = clips
        captured["style"] = style_name
        return [{**c, "file": "01.mp4", "thumb": "01.jpg", "duration": round(c["end"] - c["start"], 2),
                 "style": c["caption_style"], "reframe": "center", "watermark": False,
                 "transcript": c["transcript"]} for c in clips]

    monkeypatch.setattr(pipeline, "render_clips", fake_render)
    finished = pipeline.render_from_picks(run / "picks.json")
    assert len(captured["clips"]) == 1
    only = captured["clips"][0]
    assert only["title"] == "Trimmed" and only["caption_style"] == "mrbeast"
    assert only["start"] == 5.45 and only["end"] == 6.2
    assert only["word_range"] == [3, 3]
    assert finished["clips"][0]["title"] == "Trimmed"
    assert (run / "clips.json").is_file()
    # Refreshing the missing still changes the document, so render writes the next snapshot.
    assert (run / "picks.v2.json").is_file()
    assert json.loads((run / "picks.json").read_text())["picks"][1]["still"] == "stills/p02.jpg"


def test_cli_select_only_and_picks(monkeypatch, tmp_path):
    seen = {}

    def fake_make(*a, **k):
        seen["make"] = (a, k)
        return {"stage": "picks_ready", "format": "picks", "kind": "shorts", "run_id": "run",
                "out_dir": str(tmp_path), "source": {"title": "T"},
                "picks": [{"id": "p01", "order": 1, "enabled": True, "start": 1, "end": 2, "score": 80,
                           "title": "Hello", "hook": "opens", "caption_style": "hormozi", "still": None}]}

    def fake_render(path, out, **k):
        seen["render"] = (path, out, k)
        return {"out_dir": str(tmp_path), "clips": [], "source": {"title": "T"}}

    monkeypatch.setattr(pipeline, "make", fake_make)
    monkeypatch.setattr(pipeline, "render_from_picks", fake_render)
    cli.main(["vid.mp4", "--select-only", "--out", str(tmp_path), "--json"])
    assert seen["make"][1]["select_only"] is True
    cli.main(["--picks", str(tmp_path / "picks.json"), "--json"])
    assert seen["render"][0] == str(tmp_path / "picks.json")
    assert seen["render"][1] is None
    assert seen["render"][2]["expect_kind"] == "shorts"
    try:
        cli.main(["--select-only", "--picks", "picks.json"])
        raise AssertionError("expected SystemExit")
    except SystemExit:
        pass


def test_tool_stage_and_status(monkeypatch, tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv("SHORTRIZZ_RUNS", str(runs))
    monkeypatch.setenv("SHORTRIZZ_HOME", str(tmp_path / "home"))
    seen = {}
    monkeypatch.setattr(H, "_start_job", lambda kind, argv, out: seen.update(kind=kind, argv=list(argv), out=out) or
                        {"job_id": "make-x", "kind": kind, "pid": 1, "out_dir": str(out),
                         "log": str(Path(out) / "l"), "started": 0})
    monkeypatch.setattr(H, "_wait", lambda job, w: {"status": "running", "job_id": job["job_id"]})

    select = json.loads(H.make_shorts_tool({"source": "https://example.com/v", "stage": "select",
                                            "out_dir": str(runs / "sel")}))
    assert select["status"] == "running"
    assert "--select-only" in seen["argv"]
    assert seen["argv"][0] == "https://example.com/v"

    missing = json.loads(H.make_shorts_tool({"stage": "render"}))
    assert missing["ok"] is False and "picks_path" in missing["error"]

    picks_path = runs / "sel" / "picks.json"
    picks_path.parent.mkdir(parents=True)
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")
    doc = picks.build_document(
        run_id="sel", kind="shorts", source=picks.source_block({"title": "T"}, src, 10),
        settings={"style": "hormozi"}, clips=[_clip(0.8, 2.3, "Hello", 80, (0, 1))])
    picks.save_picks(picks_path.parent, doc, refresh_stills=False)
    outside = tmp_path / "picks.json"
    outside.write_text(picks_path.read_text())
    escaped = json.loads(H.make_shorts_tool({"stage": "render", "picks_path": str(outside)}))
    assert escaped["ok"] is False and "picks_path must stay inside" in escaped["error"]

    rendered = json.loads(H.make_shorts_tool({"stage": "render", "picks_path": str(picks_path),
                                              "source": "not-a-file"}))
    assert rendered["status"] == "running"
    argv = seen["argv"]
    assert argv[0] == "--picks" and argv[1] == str(picks_path.resolve())
    assert "--style" not in argv and "not-a-file" not in argv

    hl = json.loads(H.make_highlights_tool({"source": "https://example.com/v", "stage": "select",
                                            "out_dir": str(runs / "hl")}))
    assert hl["status"] == "running" and seen["argv"][0] == "highlights" and "--select-only" in seen["argv"]

    out = runs / "sel"
    (out / "progress.json").write_text(json.dumps({"stage": "picks_ready", "done": 1, "total": 1}))
    job = {"job_id": "make-ready", "kind": "make", "pid": 1, "out_dir": str(out),
           "log": str(out / "job.log"), "started": 0}
    (out / "job.log").write_text("picks ready\n")
    state = H._job_state(job)
    assert state["status"] == "done" and state["stage"] == "picks_ready"
    assert state["picks"][0]["id"] == "p01" and state["picks"][0]["title"] == "Hello"
    assert state["picks"][0]["still"] is None or state["picks"][0]["still"].endswith("p01.jpg")
