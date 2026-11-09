"""surfslop: the command line.

    surfslop serve [--tailnet | --lan] [--port N]   run the portal in the foreground
    surfslop serve --public [--hostname NAME]        the public portal, for a tunnel (PUBLIC.md)
    surfslop status                                  addresses, sources, who may call
    surfslop forecast SPOT                           a spot's forecast, as Markdown
    surfslop overview [--days N]                     the featured spots
    surfslop source NAME on|off                      switch a source (surfline, surfforecast, openmeteo, nws)
    surfslop peers [DEVICE...] | --clear             limit callers to these tailnet devices
    surfslop service install [--tailnet|--lan] | uninstall | restart     run at login (launchd)
"""

from __future__ import annotations

import argparse
import logging
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

from . import config, fusion, netguard, spots, text

LABEL = "com.surfslop.portal"
SURFLINE_NOTE = """Surfline is now ON.

surfslop reads the forecast surfline.com shows a signed-out visitor: 5 days, no account, no
password. It identifies itself as surfslop and reads slowly, in the background of the running
portal: one spot every couple of minutes, each spot again after 3 hours. Surfline turns automated
clients away at times; when it does, surfslop stops asking for 6 hours and keeps the last copy.
Surfline has no public API and its terms restrict automated access, so this is your call to make
for your own personal use; `surfslop source surfline off` undoes it."""


def _plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _launchctl(*args: str) -> None:
    subprocess.run(["launchctl", *args], check=False, capture_output=True)


def cmd_serve(args) -> int:
    from . import server
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    listen = "tailnet" if args.tailnet else "lan" if args.lan else None
    if args.public and listen:
        print("surfslop: --public listens on 127.0.0.1 only, for the tunnel; it can't be combined with --tailnet or "
              "--lan. Run a second `surfslop serve --tailnet` for your own use.", file=sys.stderr)
        return 2
    try:
        server.serve(listen, args.port, public=args.public, hostnames=args.hostname or (),
                     with_stats=not args.no_stats, stats_port=args.stats_port)
    except (RuntimeError, OSError) as e:
        print(f"surfslop: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_status(_args) -> int:
    cfg = config.load()
    tail = netguard.tailscale_ip()
    print(f"Listens: {cfg.listen} (port {cfg.port})")
    print(f"  on this Mac:   http://127.0.0.1:{cfg.port}")
    if cfg.listen != "local":
        print(f"  over Tailscale: http://{tail}:{cfg.port}" if tail else "  Tailscale isn't running")
    print("Who may call: " + (", ".join(cfg.peers) + " and this Mac" if cfg.peers else
                              "any device on your tailnet or local network" if cfg.listen != "local" else "this Mac only"))
    print(f"Local AI: {cfg.llm_url or 'off'}")
    from . import daily
    last = daily.built()
    print(f"Daily edition: at {cfg.daily_at} Pacific; last built "
          + (time.strftime("%a %-I:%M %p", time.localtime(last)) if last else "never (it is built when the portal runs)"))
    data = fusion.forecast(spots.FEATURED[0], cfg)
    print("Sources:")
    for s in data["sources"]:
        print(f"  {'ok ' if s['ok'] else 'DOWN'} {s['label']}" + (f" (through {s['through']})" if s["ok"] else f": {s['error']}"))
    for name in data["off"]:
        print(f"  off  {name}   (surfslop source {name} on)")
    print(f"Service: {'installed' if _plist().exists() else 'not installed'}   Files: {config.home()}")
    return 0


def cmd_forecast(args) -> int:
    spot = spots.find(" ".join(args.spot))
    if not spot:
        print("No such spot. Try one of: " + ", ".join(s.id for s in spots.SPOTS), file=sys.stderr)
        return 1
    print(text.forecast_md(fusion.forecast(spot)))
    return 0


def cmd_overview(args) -> int:
    print(text.overview_md(fusion.overview(days=args.days)))
    return 0


def cmd_source(args) -> int:
    cfg = config.load()
    if args.name not in config.SOURCES:
        print("Sources: " + ", ".join(config.SOURCES), file=sys.stderr)
        return 1
    off = set(cfg.disabled)
    off.discard(args.name) if args.state == "on" else off.add(args.name)
    cfg.disabled = sorted(off)
    config.save(cfg)
    print(SURFLINE_NOTE if (args.name, args.state) == ("surfline", "on") else f"{args.name} is now {args.state}.")
    return 0


def cmd_peers(args) -> int:
    cfg = config.load()
    if args.clear:
        cfg.peers = []
    elif args.devices:
        try:
            cfg.peers = sorted({netguard.device_ip(d) for d in args.devices})
        except ValueError as e:
            print(f"surfslop: {e}", file=sys.stderr)
            return 1
    config.save(cfg)
    print("Allowed callers: " + (", ".join(cfg.peers) + " (and this Mac)" if cfg.peers else
                                 "every device on your tailnet or local network"))
    return 0


def cmd_service(args) -> int:
    plist = _plist()
    target = f"gui/{os.getuid()}"
    if args.action == "uninstall":
        _launchctl("bootout", f"{target}/{LABEL}")
        plist.unlink(missing_ok=True)
        print("Service removed.")
        return 0
    if args.action == "restart":
        _launchctl("kickstart", "-k", f"{target}/{LABEL}")
        print("Service restarted.")
        return 0
    cfg = config.load()
    if args.tailnet or args.lan:
        cfg.listen = "tailnet" if args.tailnet else "lan"
    config.save(cfg)
    log = Path.home() / "Library" / "Logs" / "surfslop.log"
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(plistlib.dumps({
        "Label": LABEL, "ProgramArguments": [sys.executable, "-m", "surfslop", "serve"],
        "RunAtLoad": True, "KeepAlive": True, "StandardOutPath": str(log), "StandardErrorPath": str(log),
        "EnvironmentVariables": {k: v for k, v in os.environ.items() if k == "SURFSLOP_HOME"},
    }))
    _launchctl("bootout", f"{target}/{LABEL}")
    _launchctl("bootstrap", target, str(plist))
    print(f"Service installed: starts now and at every login, listening as '{cfg.listen}'. Log: {log}")
    return cmd_status(args)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(prog="surfslop", description="Bay Area surf forecast portal for people and agents.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("serve", help="run the portal in the foreground")
    p.add_argument("--tailnet", action="store_true", help="also listen on this Mac's Tailscale address")
    p.add_argument("--lan", action="store_true", help="listen on every interface (private and Tailscale callers only)")
    p.add_argument("--port", type=int)
    p.add_argument("--public", action="store_true", help="the public portal on 127.0.0.1, for a Cloudflare Tunnel (PUBLIC.md)")
    p.add_argument("--hostname", action="append", metavar="NAME", help="with --public: a host name visitors use (repeatable)")
    p.add_argument("--no-stats", action="store_true", help="with --public: don't serve the stats page")
    p.add_argument("--stats-port", type=int, help="with --public: the stats page's port (default 7875)")
    p.set_defaults(fn=cmd_serve)
    sub.add_parser("status", help="addresses, sources, who may call").set_defaults(fn=cmd_status)
    p = sub.add_parser("forecast", help="a spot's forecast as Markdown")
    p.add_argument("spot", nargs="+")
    p.set_defaults(fn=cmd_forecast)
    p = sub.add_parser("overview", help="the featured spots, next days")
    p.add_argument("--days", type=int, default=3)
    p.set_defaults(fn=cmd_overview)
    p = sub.add_parser("source", help="switch a source on or off")
    p.add_argument("name")
    p.add_argument("state", choices=("on", "off"))
    p.set_defaults(fn=cmd_source)
    p = sub.add_parser("peers", help="limit callers to these tailnet devices (name or IP)")
    p.add_argument("devices", nargs="*")
    p.add_argument("--clear", action="store_true")
    p.set_defaults(fn=cmd_peers)
    p = sub.add_parser("service", help="run at login (launchd)")
    p.add_argument("action", choices=("install", "uninstall", "restart"))
    p.add_argument("--tailnet", action="store_true")
    p.add_argument("--lan", action="store_true")
    p.set_defaults(fn=cmd_service)
    args = parser.parse_args(argv)
    return args.fn(args)
