"""Count o200k tokens in the registered Hermes tool schemas.

Usage: python3 evals/measure_schemas.py

Prints one integer: tiktoken o200k_base tokens of the JSON array of schemas
(compact separators, the same measurement as the catalog baseline).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tiktoken

from shortrizz.hermes_tools import tool_specs


def schema_tokens() -> int:
    enc = tiktoken.get_encoding("o200k_base")
    blob = json.dumps([s["schema"] for s in tool_specs()], ensure_ascii=False, separators=(",", ":"))
    return len(enc.encode(blob))


def main() -> None:
    print(schema_tokens())


if __name__ == "__main__":
    main()
