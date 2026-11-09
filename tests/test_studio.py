"""Studio REST: confinement, picks versions, proxy cache, and the pane routes."""
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shortrizz import hermes_tools as H
from shortrizz import picks
from shortrizz import studio

ROOT = Path(__file__).resolve().parents[1]
SECRET = "sk-studio-test-secret"


def _words():
    return [
        {"w": "hello", "s": 1.0, "e": 1.4},
        {"w": "world", "s": 1.5, "e": 2.0},
        {"w": "again", "s": 5.0, "e": 5.4},
        {"w": "friend", "s": 5.5, "e": 6.0},
    ]


def _clip(start, end, title, score, wr):
    words = _words()
    a, b = wr
    return {
        "start": start, "end": end, "title": title, "virality_score": score,
        "why_it_hooks": f"hook for {title}", "caption": "watch this", "hashtags": ["#t"],
        "method": "heuristic", "word_range": [a, b],
        "transcript": " ".join(w["w"] for w in words[a:b + 1]),
    }


def _jpeg(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"\xff\xd8" + b"j" * 80)


def _load_api():
    spec = importlib.util.spec_from_file_location("shortrizz_plugin_api", ROOT / "dashboard" / "plugin_api.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _client():
    app = FastAPI()
    app.include_router(_load_api().router, prefix="/api/plugins/shortrizz")
    return TestClient(app)


def _seed(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv("SHORTRIZZ_RUNS", str(runs))
    monkeypatch.setenv("SHORTRIZZ_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENCODE_GO_API_KEY", SECRET)
    src = tmp_path / "source.mp4"
    src.write_bytes(b"videobytes")
    run = runs / "run-1"
    doc = picks.build_document(
        run_id="run-1", kind="shorts",
        source=picks.source_block({"title": "Talk", "webpage_url": "https://example.com/watch"}, src, 12),
        settings={"style": "hormozi", "count": 2},
        clips=[_clip(0.8, 2.3, "Hello", 88, (0, 1)), _clip(4.8, 6.2, "Again", 70, (2, 3))],
        words=_words(),
    )
    doc["picks"][0]["still"] = "stills/p01.jpg"
    doc["picks"][1]["still"] = "stills/p02.jpg"
    picks.save_picks(run, doc, refresh_stills=False)
    picks.write_words(run, _words())
    _jpeg(run / "stills" / "p01.jpg")
    _jpeg(run / "stills" / "p02.jpg")
    (run / "progress.json").write_text(json.dumps({"stage": "picks_ready", "done": 2, "total": 2}))
    return runs, run, src


def test_stage_from_matrix():
    assert studio.stage_from(raw="download", alive=True, has_picks=False, has_output=False) == "selecting"
    assert studio.stage_from(raw="picks_ready", alive=False, has_picks=True, has_output=False) == "picks_ready"
    assert studio.stage_from(raw="render", alive=True, has_picks=True, has_output=False) == "rendering"
    assert studio.stage_from(raw="done", alive=False, has_picks=True, has_output=True) == "done"
    assert studio.stage_from(raw="transcribe", alive=False, has_picks=False, has_output=False) == "error"
    assert studio.stage_from(raw="render", alive=False, has_picks=True, has_output=False) == "error"


def test_snap_edge_uses_word_boundaries():
    words = _words()
    assert studio.snap_edge(words, 1.2, "start") == 1.0
    assert studio.snap_edge(words, 2.2, "end") == 2.0
    assert studio.snap_edge(words, 9.0, "start") == 9.0
    assert studio.snap_edge([], 1.25, "start") == 1.25


def test_proxy_args_are_a_short_uncaptioned_scale():
    args = studio.proxy_ffmpeg_args("/tmp/in.mp4", 1.5, 4.0, "/tmp/out.mp4")
    assert "ultrafast" in args
    assert "scale=360:-2,format=yuv420p" in args
    assert "-ss" in args and "-t" in args
    assert not any("ass" in part for part in args)
    assert args[args.index("-t") + 1] == "2.500"


def test_runs_list_and_storyboard_hide_secrets_and_source_path(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    client = _client()
    listed = client.get("/api/plugins/shortrizz/runs")
    assert listed.status_code == 200
    body = listed.json()
    assert body["ok"] is True
    assert body["runs"][0]["id"] == "run-1"
    assert body["runs"][0]["stage"] == "picks_ready"
    assert body["runs"][0]["title"] == "Talk"
    blob = json.dumps(body)
    assert SECRET not in blob
    assert "video_path" not in blob

    detail = client.get("/api/plugins/shortrizz/runs/run-1").json()
    assert detail["run"]["revision"] == 1
    assert [p["id"] for p in detail["run"]["picks"]] == ["p01", "p02"]
    assert detail["run"]["picks"][0]["words"][0]["w"] == "hello"
    assert SECRET not in json.dumps(detail)
    assert "source.mp4" not in json.dumps(detail)

    health = client.get("/api/plugins/shortrizz/health").json()
    assert health["deps"]["opencode_go_api_key"] == {"name": "OPENCODE_GO_API_KEY", "set": True}
    assert SECRET not in json.dumps(health)
    assert {row["id"] for row in health["styles"]} >= {"hormozi", "mrbeast", "abdaal"}


def test_media_rejects_traversal_and_serves_a_still(tmp_path, monkeypatch):
    runs, run, _src = _seed(tmp_path, monkeypatch)
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(b"SECRET-BYTES-NOT-FOR-PANE")
    (run / "stills" / "leak.jpg").symlink_to(outside)
    client = _client()
    ok = client.get("/api/plugins/shortrizz/media", params={"run": "run-1", "path": "stills/p01.jpg"})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert ok.json()["mime"] == "image/jpeg"
    assert ok.json()["data_b64"]

    for rel in ("../secret.jpg", "stills/../../secret.jpg", "/etc/passwd", "stills/leak.jpg", "picks.json"):
        denied = client.get("/api/plugins/shortrizz/media", params={"run": "run-1", "path": rel}).json()
        assert denied["ok"] is False, rel
        assert "SECRET-BYTES" not in json.dumps(denied)
    missing = client.get("/api/plugins/shortrizz/runs/not-a-run").json()
    assert missing["ok"] is False
    assert client.get("/api/plugins/shortrizz/runs/%2e%2e").json()["ok"] is False


def test_save_writes_next_version_and_restore_keeps_history(tmp_path, monkeypatch):
    _runs, run, _src = _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(picks, "still_frame", lambda *a, **k: _jpeg(a[2]))
    client = _client()
    detail = client.get("/api/plugins/shortrizz/runs/run-1").json()["run"]
    picks_body = detail["picks"]
    picks_body[0]["title"] = "Renamed"
    picks_body[0]["start"] = 1.2  # snaps to the word at 1.0
    picks_body[0]["caption_style"] = "mrbeast"
    picks_body[0]["enabled"] = False
    picks_body[1]["order"] = 0
    saved = client.post("/api/plugins/shortrizz/runs/run-1/picks", json={"picks": picks_body}).json()
    assert saved["ok"] is True
    assert saved["run"]["revision"] == 2
    first = next(p for p in saved["run"]["picks"] if p["id"] == "p01")
    assert first["title"] == "Renamed"
    assert first["start"] == 1.0
    assert first["caption_style"] == "mrbeast"
    assert first["enabled"] is False
    assert (run / "picks.v2.json").is_file()
    assert json.loads((run / "picks.v1.json").read_text())["picks"][0]["title"] == "Hello"

    restored = client.post("/api/plugins/shortrizz/runs/run-1/restore", json={"revision": 1}).json()
    assert restored["ok"] is True
    assert restored["run"]["revision"] == 3
    assert restored["run"]["picks"][0]["title"] == "Hello"
    assert (run / "picks.v3.json").is_file()
    bad = client.post("/api/plugins/shortrizz/runs/run-1/picks", json={"picks": [{"id": "nope"}]}).json()
    assert bad["ok"] is False


def test_preview_caches_under_the_run(tmp_path, monkeypatch):
    _runs, run, src = _seed(tmp_path, monkeypatch)
    calls = []

    def fake_encode(video, start, end, dest):
        calls.append((str(video), start, end, Path(dest)))
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"proxy-bytes")

    monkeypatch.setattr(studio, "encode_proxy", fake_encode)
    client = _client()
    first = client.post("/api/plugins/shortrizz/runs/run-1/preview", json={"pick_id": "p01"}).json()
    second = client.post("/api/plugins/shortrizz/runs/run-1/preview", json={"pick_id": "p01"}).json()
    assert first["ok"] and second["ok"]
    assert first["path"] == second["path"]
    assert first["path"].startswith("previews/p01_")
    assert len(calls) == 1
    assert calls[0][0] == str(src)
    assert calls[0][3].is_relative_to(run)
    media = client.get("/api/plugins/shortrizz/media", params={"run": "run-1", "path": first["path"]}).json()
    assert media["ok"] and media["mime"] == "video/mp4"
    assert SECRET not in json.dumps(first)


def test_select_render_and_restyle_routes(tmp_path, monkeypatch):
    runs, run, _src = _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(picks, "still_frame", lambda *a, **k: _jpeg(a[2]))
    started = {}

    def fake_tool(payload):
        started["payload"] = payload
        out = Path(payload["out_dir"]) if payload.get("out_dir") else runs / "run-new"
        out.mkdir(parents=True, exist_ok=True)
        return json.dumps({"ok": True, "job_id": "make-test", "out_dir": str(out), "status": "running", "stage": payload.get("stage")})

    monkeypatch.setattr(H, "make_shorts_tool", fake_tool)
    monkeypatch.setattr(H, "make_highlights_tool", fake_tool)
    monkeypatch.setattr(H, "render_style_tool", fake_tool)
    client = _client()

    created = client.post("/api/plugins/shortrizz/runs", json={
        "source": "https://example.com/watch", "kind": "shorts", "style": "hormozi",
    }).json()
    assert created["ok"] is True
    assert created["stage"] == "selecting"
    assert started["payload"]["stage"] == "select"
    assert started["payload"]["source"] == "https://example.com/watch"
    assert "run_id" in created and SECRET not in json.dumps(created)

    rendered = client.post("/api/plugins/shortrizz/runs/run-1/render", json={}).json()
    assert rendered["ok"] is True
    assert rendered["stage"] == "rendering"
    assert started["payload"]["stage"] == "render"
    assert started["payload"]["picks_path"].endswith("run-1/picks.json")
    assert "/renders/v1" in started["payload"]["out_dir"]
    assert (run / "renders" / "v1" / "studio.json").is_file()

    (run / "renders" / "v1" / "clips.json").write_text(json.dumps({
        "format": "shorts", "settings": {"style": "hormozi"},
        "clips": [{"file": "01-hello.mp4", "title": "Hello", "duration": 1.2, "virality_score": 88}],
    }))
    (run / "renders" / "v1" / "01-hello.mp4").write_bytes(b"mp4")
    (run / "renders" / "v1" / "progress.json").write_text(json.dumps({"stage": "done", "done": 1, "total": 1}))
    detail = client.get("/api/plugins/shortrizz/runs/run-1").json()["run"]
    assert detail["stage"] == "done"
    assert detail["renders"][-1]["clips"][0]["path"] == "renders/v1/01-hello.mp4"

    restyled = client.post("/api/plugins/shortrizz/runs/run-1/restyle", json={"style": "abdaal"}).json()
    assert restyled["ok"] is True
    assert started["payload"]["style"] == "abdaal"
    assert started["payload"]["run_dir"].endswith("renders/v1")
    assert "/renders/v2" in started["payload"]["out_dir"]

    repick = client.post("/api/plugins/shortrizz/runs/run-1/repick", json={}).json()
    assert repick["ok"] is True
    assert started["payload"]["avoid_previous"] is True
    assert started["payload"]["stage"] == "select"

    opened = client.post("/api/plugins/shortrizz/runs/run-1/open").json()
    assert opened["ok"] is True
    assert Path(opened["path"]) == run.resolve()
    assert SECRET not in json.dumps(opened)


def test_missing_tools_are_named_not_valued(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    real = __import__("shutil").which

    def which(name):
        if name in ("ffmpeg", "ffprobe", "yt-dlp"):
            return None
        return real(name)

    monkeypatch.setattr(studio, "shutil", type("S", (), {"which": staticmethod(which)})())
    monkeypatch.setattr(studio, "_has_ytdlp", lambda: False)
    client = _client()
    health = client.get("/api/plugins/shortrizz/health").json()
    assert health["deps"]["ffmpeg"] is False
    assert health["deps"]["yt_dlp"] is False
    assert health["deps"]["opencode_go_api_key"]["name"] == "OPENCODE_GO_API_KEY"
    blocked = client.post("/api/plugins/shortrizz/runs", json={"source": "https://example.com/v", "kind": "shorts"}).json()
    assert blocked["ok"] is False
    assert "ffmpeg" in blocked["error"]
    assert SECRET not in json.dumps(blocked)


def test_route_hides_unexpected_errors(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    api = _load_api()

    def boom():
        raise RuntimeError(f"blew up with {SECRET}")

    monkeypatch.setattr(api, "health", boom)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/shortrizz")
    body = TestClient(app).get("/api/plugins/shortrizz/health").json()
    assert body["ok"] is False
    assert SECRET not in json.dumps(body)
    assert "Traceback" not in json.dumps(body)


def test_select_only_writes_words_sidecar(monkeypatch, tmp_path):
    from shortrizz import pipeline
    src = tmp_path / "v.mp4"
    src.write_bytes(b"videobytes")
    words = _words()
    tr = {"segments": [{"text": "hello world again friend", "words": words}]}
    monkeypatch.setattr(pipeline.download, "fetch", lambda *a, **k: (src, {"title": "Talk", "source": "file://v"}))
    monkeypatch.setattr(pipeline.T, "transcribe", lambda *a, **k: tr)
    monkeypatch.setattr(pipeline, "ffprobe_duration", lambda p: 12.0)
    monkeypatch.setattr(pipeline.clipselect, "select", lambda *a, **k: [_clip(0.8, 2.3, "Hello", 88, (0, 1))])
    monkeypatch.setattr(pipeline.picks, "still_frame", lambda *a, **k: _jpeg(a[2]))
    out = tmp_path / "run"
    pipeline.make(str(src), out, count=1, min_len=1, max_len=8, use_llm=False, select_only=True, watermark=False)
    saved = json.loads((out / "words.json").read_text())
    assert [row["w"] for row in saved] == ["hello", "world", "again", "friend"]
