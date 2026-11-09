"""Declared plugin secrets.

Shortrizz never reads a dotenv file. ``OPENCODE_GO_API_KEY`` is declared in
``plugin.yaml`` ``requires_env``. When ``agent.secret_scope`` imports, that
module's ``get_secret`` is the only lookup. Otherwise the same declared name is
read from ``os.environ`` (the CLI, or a job whose parent already resolved it).
"""
from __future__ import annotations

import os

# Keep this tuple in step with plugin.yaml requires_env.
DECLARED_SECRETS = ("OPENCODE_GO_API_KEY",)


def get_secret(name: str) -> str | None:
    """Return a declared secret, or None.

    A name that is not in ``DECLARED_SECRETS`` is refused even when the
    environment has it. A successful ``agent.secret_scope.get_secret`` call is
    trusted, including a miss. ``os.environ`` is used only when that module
    cannot be imported.
    """
    if name not in DECLARED_SECRETS:
        return None
    try:
        from agent.secret_scope import get_secret as scope_get
    except ImportError:
        scope_get = None
    if scope_get is not None:
        value = scope_get(name)
        if value is None:
            return None
        text = str(value).strip()
        return text or None
    value = os.environ.get(name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None
