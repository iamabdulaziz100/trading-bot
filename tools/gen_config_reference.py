"""Regenerate docs/CONFIG_REFERENCE.md from the config schema (single source of truth)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import config_schema  # noqa: E402


def fmt(v) -> str:
    return "`" + json.dumps(v, ensure_ascii=False) + "`"


def main() -> None:
    lines = ["# Configuration reference", "",
             "Generated from the pydantic schema in `app/config.py` by `python tools/gen_config_reference.py` — "
             "do not edit by hand. Spec: `docs/spec/07_configuration_schema.md`.", "",
             "Legend: **R** = changing it requires *Rebuild structure* (UI button / `POST /api/structure/rebuild`), "
             "**S** = requires a restart of the bot. Everything else hot-reloads.", ""]
    for sec in config_schema()["sections"]:
        lines += [f"## {sec['title']} (`{sec['key']}`)", "", "| Key | Default | Allowed | Description | Spec | |",
                  "|---|---|---|---|---|---|"]
        for f in sec["fields"]:
            allowed = []
            if f["options"]:
                allowed.append(" / ".join(str(o) for o in f["options"]))
            if f["min"] is not None or f["max"] is not None:
                allowed.append(f"{'' if f['min'] is None else f['min']} … {'' if f['max'] is None else f['max']}")
            if f["warn_min"] is not None or f["warn_max"] is not None:
                allowed.append(f"warn outside {f['warn_min'] or ''}–{f['warn_max'] or ''}")
            flag = "R" if f["rebuild_required"] else "S" if f["restart_required"] else ""
            lines.append(f"| `{f['path']}` | {fmt(f['default'])} | {'; '.join(allowed)} | {f['description']} | "
                         f"{f['spec_ref']} | {flag} |")
        lines.append("")
    (ROOT / "docs" / "CONFIG_REFERENCE.md").write_text("\n".join(lines), encoding="utf-8")
    print("wrote docs/CONFIG_REFERENCE.md")


if __name__ == "__main__":
    main()
