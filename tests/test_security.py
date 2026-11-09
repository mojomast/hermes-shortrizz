"""Catalog review: secrets, child env, path confinement, pinned downloads."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from shortrizz import hermes_tools as H
from shortrizz import reframe, secrets, tts, util

ROOT = Path(__file__).resolve().parents[1]


def _block_scope(monkeypatch):
    import builtins
    real = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "agent" or name.startswith("agent."):
            raise ImportError("blocked")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)


def test_undeclared_name_is_refused(monkeypatch):
    _block_scope(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    assert secrets.get_secret("OPENAI_API_KEY") is None


def test_environ_fallback_only_without_scope(monkeypatch):
    _block_scope(monkeypatch)
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "  from-env  ")
    assert secrets.get_secret("OPENCODE_GO_API_KEY") == "from-env"
    monkeypatch.delenv("OPENCODE_GO_API_KEY")
    assert secrets.get_secret("OPENCODE_GO_API_KEY") is None


def test_scope_hit_and_miss_do_not_read_environ(monkeypatch):
    import types
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "from-env")
    mod = types.ModuleType("agent.secret_scope")
    mod.get_secret = lambda name, default=None: "scoped-value"
    agent = types.ModuleType("agent")
    agent.secret_scope = mod
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", mod)
    assert secrets.get_secret("OPENCODE_GO_API_KEY") == "scoped-value"
    mod.get_secret = lambda name, default=None: None
    assert secrets.get_secret("OPENCODE_GO_API_KEY") is None


def test_unscoped_secret_error_propagates(monkeypatch):
    import types

    class UnscopedSecretError(RuntimeError):
        pass

    def boom(name, default=None):
        raise UnscopedSecretError("multiplex")

    mod = types.ModuleType("agent.secret_scope")
    mod.get_secret = boom
    agent = types.ModuleType("agent")
    agent.secret_scope = mod
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", mod)
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "from-env")
    with pytest.raises(UnscopedSecretError):
        secrets.get_secret("OPENCODE_GO_API_KEY")


def test_dotenv_file_is_not_read(monkeypatch, tmp_path):
    _block_scope(monkeypatch)
    envfile = tmp_path / ".env"
    envfile.write_text("OPENCODE_GO_API_KEY=from-dotenv\nOPENAI_API_KEY=nope\n")
    monkeypatch.setenv("SHORTRIZZ_ENV_FILE", str(envfile))
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    assert secrets.get_secret("OPENCODE_GO_API_KEY") is None
    blob = "\n".join(
        (ROOT / rel).read_text()
        for rel in ("shortrizz/util.py", "shortrizz/clipselect.py", "README.md", "skills/shortrizz-clipper/SKILL.md")
    )
    assert "load_env_file" not in blob
    assert "SHORTRIZZ_ENV_FILE" not in blob


def test_child_env_is_an_allowlist(monkeypatch, tmp_path):
    monkeypatch.setenv("SHORTRIZZ_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SHORTRIZZ_RUNS", str(tmp_path / "runs"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "from-env")
    monkeypatch.setenv("SHORTRIZZ_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("SHORTRIZZ_LLM_MODEL", "deepseek-v4.1-flash")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("PYTHONHOME", "/should-not-pass")
    monkeypatch.setenv("SSL_CERT_FILE", "/etc/ssl/cert.pem")
    monkeypatch.setattr(H, "get_secret", lambda name: "from-scope" if name == "OPENCODE_GO_API_KEY" else None)
    captured = {}

    class Proc:
        pid = 1

    def fake_popen(argv, **kw):
        captured["env"] = kw["env"]
        captured["argv"] = argv
        return Proc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    out = tmp_path / "runs" / "job"
    H._start_job("make", ["--json"], out)
    env = captured["env"]
    assert "OPENAI_API_KEY" not in env and "ANTHROPIC_API_KEY" not in env
    assert env["OPENCODE_GO_API_KEY"] == "from-scope"
    assert "SHORTRIZZ_ENV_FILE" not in env
    assert env["SHORTRIZZ_LLM_MODEL"] == "deepseek-v4.1-flash"
    assert env["SHORTRIZZ_RUNS"] == str(tmp_path / "runs")
    assert env["LC_ALL"] == "C.UTF-8"
    assert "PYTHONHOME" not in env
    assert env["PYTHONUNBUFFERED"] == "1"
    assert "shortrizz" in env["PYTHONPATH"] or str(ROOT) in env["PYTHONPATH"]
    assert env.get("PATH")
    assert env["SSL_CERT_FILE"] == "/etc/ssl/cert.pem"
    assert captured["argv"][1:3] == ["-m", "shortrizz"]


def test_confine_rejects_escape_and_allows_the_root(monkeypatch, tmp_path):
    runs = tmp_path / "runs"
    cache = tmp_path / "cache"
    runs.mkdir()
    cache.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("SHORTRIZZ_RUNS", str(runs))
    monkeypatch.setenv("SHORTRIZZ_CACHE", str(cache))
    monkeypatch.setattr(H, "_wait", lambda job, w: {"status": "running", "job_id": job["job_id"]})
    seen = {}
    monkeypatch.setattr(H, "_start_job", lambda kind, argv, out: seen.update(argv=argv, out=out) or
                        {"job_id": "make-x", "kind": kind, "pid": 1, "out_dir": str(out),
                         "log": str(Path(out) / "l"), "started": 0})

    def call(**extra):
        return json.loads(H.make_shorts_tool({"source": "https://example.com/v", **extra}))

    escaped = call(out_dir=str(outside))
    assert escaped["ok"] is False and "out_dir must stay inside" in escaped["error"]
    traversal = call(out_dir=str(runs / ".." / "outside"))
    assert traversal["ok"] is False and "outside" in traversal["error"]
    link = runs / "escape"
    link.symlink_to(outside)
    linked = call(out_dir=str(link))
    assert linked["ok"] is False and "out_dir must stay inside" in linked["error"]
    empty = call(out_dir="  ")
    assert empty["ok"] is False and "empty" in empty["error"]
    cache_bad = call(out_dir=str(runs / "ok"), cache_dir=str(outside))
    assert cache_bad["ok"] is False and "cache_dir must stay inside" in cache_bad["error"]

    ok = call(out_dir=str(runs), cache_dir=str(cache))
    assert ok["status"] == "running"
    assert seen["out"] == runs.resolve()
    assert seen["argv"][seen["argv"].index("--cache") + 1] == str(cache.resolve())

    run = runs / "finished"
    run.mkdir()
    (run / "clips.json").write_text("{}")
    bad_style = json.loads(H.render_style_tool({"run_dir": str(run), "style": "mrbeast", "out_dir": str(outside)}))
    assert bad_style["ok"] is False and "out_dir must stay inside" in bad_style["error"]


def test_hash_mismatch_deletes_partial(monkeypatch, tmp_path):
    def fake_retrieve(url, dest):
        Path(dest).write_bytes(b"not-the-model")

    monkeypatch.setattr("urllib.request.urlretrieve", fake_retrieve)
    dest = tmp_path / "face.onnx"
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        util.download_verified("https://example.invalid/face.onnx", dest, "ab" * 32, "YuNet")
    assert not dest.exists()
    assert not Path(str(dest) + ".part").exists()

    corrupt = tmp_path / "voices.bin"
    corrupt.write_bytes(b"corrupt")

    def boom(*_a, **_k):
        raise RuntimeError("refused")

    monkeypatch.setattr(util, "download_verified", boom)
    with pytest.raises(RuntimeError, match="refused"):
        util.ensure_verified(corrupt, "https://example.invalid/voices.bin", "cd" * 32, "Kokoro voices")
    assert not corrupt.exists()


def test_yunet_and_kokoro_are_pinned():
    assert reframe.YUNET_COMMIT in reframe.YUNET_URL
    assert "raw/main" not in reframe.YUNET_URL
    assert len(reframe.YUNET_SHA256) == 64
    assert set(tts.FILES) == {"kokoro-v1.0.onnx", "voices-v1.0.bin"}
    assert all(len(digest) == 64 for digest in tts.FILES.values())
    extra = (ROOT / "pyproject.toml").read_text()
    assert 'kokoro-onnx>=0.4,<1' in extra
