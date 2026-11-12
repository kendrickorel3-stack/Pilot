"""Portal configuration, loaded from a TOML file (see portals.example.toml)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Portal:
    id: str
    client: str
    name: str
    login_url: str
    allowed_domains: list[str]
    instructions: str
    since: str = ""
    max_steps: int = 40
    extra: dict = field(default_factory=dict)

    def task(self) -> str:
        return self.instructions.format(since=self.since or "the start of last month")


def load_portals(path: str | Path) -> list[Portal]:
    data = tomllib.loads(Path(path).read_text())
    portals = []
    for raw in data.get("portal", []):
        known = {k: raw[k] for k in ("id", "client", "name", "login_url", "allowed_domains", "instructions", "since", "max_steps") if k in raw}
        missing = {"id", "client", "name", "login_url", "allowed_domains", "instructions"} - known.keys()
        if missing:
            raise ValueError(f"Portal {raw.get('id', '?')} is missing: {', '.join(sorted(missing))}")
        if not known["allowed_domains"]:
            raise ValueError(f"Portal {known['id']} needs at least one allowed domain")
        portals.append(Portal(**known, extra={k: v for k, v in raw.items() if k not in known}))
    ids = [p.id for p in portals]
    if len(ids) != len(set(ids)):
        raise ValueError("Portal ids must be unique")
    return portals
