from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


def load_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        config = tomllib.load(handle)

    required = ("digest", "models", "email", "theme", "sections", "sources")
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"Missing config sections: {', '.join(missing)}")

    enabled = [source for source in config["sources"] if source.get("enabled", True)]
    if not enabled:
        raise ValueError("At least one source must be enabled in config.toml")
    for source in enabled:
        if not source.get("name") or not source.get("url"):
            raise ValueError("Every enabled source needs a name and URL")

    config["sources"] = enabled
    return config
