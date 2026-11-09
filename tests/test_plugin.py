import importlib.util, json, sys
from pathlib import Path
import yaml
from shortrizz import hermes_tools as H

ROOT = Path(__file__).resolve().parents[1]

class FakeCtx:
    def __init__(self):
        self.tools, self.skills = {}, {}
    def register_tool(self, name, toolset, schema, handler, check_fn=None, requires_env=None, is_async=False,
                      description="", emoji="", override=False):
        assert schema["name"] == name and schema["parameters"]["type"] == "object"
        self.tools[name] = handler
    def register_skill(self, name, path):
        self.skills[name] = Path(path)

def load_plugin():
    spec = importlib.util.spec_from_file_location("shortrizz_plugin", ROOT / "__init__.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

def test_manifest_matches_registered_tools_and_skill():
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text())
    ctx = FakeCtx(); load_plugin().register(ctx)
    assert sorted(ctx.tools) == sorted(manifest["provides_tools"])
    assert "shortrizz-clipper" in ctx.skills
    skill = ctx.skills["shortrizz-clipper"].read_text()
    assert H.SKILL_ID in skill and skill.startswith("---")
    py = (ROOT / "pyproject.toml").read_text()
    for dep in manifest["python_dependencies"]:
        assert dep in py and "<" in dep  # pinned with an upper bound, in step with pyproject

def test_plugin_yaml_declares_version_tools_and_secret():
    from shortrizz import __version__
    from shortrizz.secrets import DECLARED_SECRETS
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text())
    assert manifest["name"] == "shortrizz"
    assert manifest["version"] == __version__ == "0.5.0"
    assert manifest["requires_hermes"] == ">=0.23.0"
    assert manifest["provides_hooks"] == []
    assert manifest["provides_tools"] == list(H.TOOL_NAMES)
    assert len(H.TOOL_NAMES) == 6
    env = manifest["requires_env"]
    assert [item["name"] for item in env] == list(DECLARED_SECRETS) == ["OPENCODE_GO_API_KEY"]
    item = env[0]
    assert item["secret"] is True
    assert item["name"].isupper() and item["name"].replace("_", "").isalnum()
    assert "Hermes .env" not in manifest["description"]
    assert "kokoro-onnx>=0.4,<1" in (ROOT / "pyproject.toml").read_text()

def test_register_does_not_import_heavy_modules():
    for m in ("cv2", "faster_whisper"):
        sys.modules.pop(m, None)
    load_plugin().register(FakeCtx())
    assert "faster_whisper" not in sys.modules

def call(name, **args):
    return json.loads(dict((s["name"], s["handler"]) for s in H.tool_specs())[name](args))

def test_list_styles():
    r = call("shortrizz_list_styles")
    assert r["ok"] and set(r["builtin"]) >= {"hormozi", "mrbeast", "abdaal"}

def test_make_shorts_validates_inputs(tmp_path):
    assert call("shortrizz_make_shorts")["ok"] is False
    assert "not found" in call("shortrizz_make_shorts", source=str(tmp_path / "x.mp4"))["error"]
    assert "style" in call("shortrizz_make_shorts", source="https://example.com/v", style="comic-sans")["error"]
    assert call("shortrizz_make_shorts", source="https://example.com/v", count=99)["ok"] is False
    assert call("shortrizz_make_shorts", source="https://example.com/v", min_seconds=70, max_seconds=40)["ok"] is False

def test_status_rejects_bad_job_ids(monkeypatch, tmp_path):
    monkeypatch.setenv("SHORTRIZZ_HOME", str(tmp_path))
    assert call("shortrizz_status", job_id="../../etc/passwd")["ok"] is False
    assert call("shortrizz_status", job_id="make-nope")["ok"] is False

def test_job_lifecycle_reports_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("SHORTRIZZ_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SHORTRIZZ_RUNS", str(tmp_path / "runs"))
    f = tmp_path / "silent.mp4"; f.write_bytes(b"not a video")
    r = call("shortrizz_make_shorts", source=str(f), wait_seconds=60)
    assert r["status"] == "failed" and r["ok"] is False and r["log_tail"]
    r2 = call("shortrizz_status", job_id=r["job_id"])
    assert r2["status"] == "failed"

def test_publish_plan_tool(tmp_path):
    (tmp_path / "clips.json").write_text(json.dumps({"clips": [
        {"file": "a.mp4", "title": "A", "caption": "c", "hashtags": [], "virality_score": 50}]}))
    r = call("shortrizz_publish_plan", run_dir=str(tmp_path), platforms=["instagram"])
    assert r["ok"] and r["dry_run"] and r["posts"][0]["platform"] == "instagram"
    assert call("shortrizz_publish_plan", run_dir=str(tmp_path), platforms=["myspace"])["ok"] is False
