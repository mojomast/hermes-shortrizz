"""Hermes plugin entrypoint for Shortrizz. Registers the skill and the shortrizz_* tools."""

from pathlib import Path


def register(ctx) -> None:
    """Register skills/<name>/SKILL.md and the shortrizz_* tools.

    Tool handlers import OpenCV / faster-whisper only inside the background job process,
    so loading the plugin stays fast.
    """
    skills_dir = Path(__file__).resolve().parent / "skills"
    if skills_dir.is_dir() and hasattr(ctx, "register_skill"):
        for child in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            skill_md = child / "SKILL.md"
            if skill_md.is_file():
                ctx.register_skill(child.name, skill_md)
    from shortrizz.hermes_tools import register_tools

    register_tools(ctx)
