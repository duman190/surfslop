"""Settings and where files live.

Everything is in one private folder (0700): ``config.json`` (0600), the HTTP cache,
satellite images and AI overviews. ``SURFSLOP_HOME`` overrides the folder.

No password or token is stored: every source is read without signing in.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_PORT = 7873  # S-U-R-F on a phone keypad
TZ = "America/Los_Angeles"
SOURCES = ("surfline", "surfforecast", "openmeteo", "nws")


def home() -> Path:
    override = os.environ.get("SURFSLOP_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "surfslop"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "surfslop"


def cache_dir(name: str = "") -> Path:
    path = home() / "cache" / name
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def config_path() -> Path:
    return home() / "config.json"


def write_private(path: Path, text: str) -> None:
    """Write a file only this user can read, replacing it in one step. Safe when several threads
    or both portals write the same file: each writer has its own temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


@dataclass
class Config:
    port: int = DEFAULT_PORT
    # "local" = this Mac only, "tailnet" = this Mac + its Tailscale address, "lan" = every interface
    # (answers private / Tailscale addresses only).
    listen: str = "local"
    # Tailnet devices that may call, by Tailscale IP. Empty = every device on your tailnet / LAN.
    peers: list = field(default_factory=list)
    # Sources that are switched off. Surfline starts off: see README, "Surfline".
    disabled: list = field(default_factory=lambda: ["surfline"])
    # The local model: the address of an OpenAI-compatible server on your network, for example an
    # inference-switchboard port ("http://<its-tailscale-ip>:8000"). Empty = no written overviews.
    llm_url: str = ""
    llm_max_tokens: int = 420
    refresh_minutes: int = 45
    # The daily edition (daily.py): every morning at this time, Pacific, the home page's spots are read
    # (Surfline included), fused, and their overviews written, so the day starts from a full cache.
    daily_at: str = "04:00"
    # --public (PUBLIC.md): the portal for the tunnel, the stats page for the tailnet, and the host
    # names visitors may use (your domain). Without a name only localhost is answered.
    public_port: int = 7874
    stats_port: int = 7875
    public_hosts: list = field(default_factory=list)

    def enabled(self, source: str) -> bool:
        return source not in self.disabled


def load() -> Config:
    cfg = Config()
    path = config_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        for key, value in data.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
    if os.environ.get("SURFSLOP_LLM_URL") is not None:
        cfg.llm_url = os.environ["SURFSLOP_LLM_URL"]
    return cfg


def edition_start(at: str = "04:00", now: float | None = None) -> float:
    """When today's edition began: the most recent `at` (HH:MM, Pacific) that has passed."""
    try:
        hour, minute = (int(x) for x in at.split(":"))
    except ValueError:
        hour, minute = 4, 0
    local = datetime.fromtimestamp(time.time() if now is None else now, ZoneInfo(TZ))
    edge = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if edge > local:
        edge -= timedelta(days=1)
    return edge.timestamp()


def save(cfg: Config) -> None:
    write_private(config_path(), json.dumps(asdict(cfg), indent=1))
