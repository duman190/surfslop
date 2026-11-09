"""Who may call: this Mac, your private network and your tailnet. Nobody else.

Two checks on every request (as in the Lynch Pin portal):
  * the caller's address is loopback, private (RFC 1918 / RFC 4193), link-local or Tailscale
    (100.64.0.0/10). On a tailnet an address can't be spoofed: Tailscale ties it to one device's key.
  * the Host header names this machine the way a local client would, which stops DNS rebinding
    (a public web page resolving its own domain to a private address).

With `peers` set (`surfslop peers muse`), callers other than this Mac must also be on that list.
This is a network boundary, not a login: the portal is read-only and holds no secrets.
"""

from __future__ import annotations

import ipaddress
import shutil
import socket
import subprocess
from pathlib import Path

TAILNET = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
TUNNEL_HEADERS = ("CF-Connecting-IP", "CF-Ray", "Cdn-Loop")
LOCAL_NETS = tuple(ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10",
    "::1/128", "fc00::/7", "fe80::/10"))
_LOCAL_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal", ".ts.net")
_CLI = ("tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale", "/opt/homebrew/bin/tailscale",
        "/usr/local/bin/tailscale")


def ip(value):
    """Parsed address (IPv4-mapped IPv6 unwrapped), or None."""
    try:
        addr = ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError:
        return None
    return getattr(addr, "ipv4_mapped", None) or addr


def is_loopback(value) -> bool:
    addr = ip(value)
    return bool(addr and addr.is_loopback)


def is_local(value) -> bool:
    addr = ip(value)
    return addr is not None and any(addr.version == n.version and addr in n for n in LOCAL_NETS)


def host_name(header: str | None) -> str:
    """The Host header without its port, lower case."""
    host = (header or "").strip().lower()
    if host.startswith("["):
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    return host.rstrip(".")


def host_ok(header: str | None) -> bool:
    """True for a name a local client would use, which a public web page can't rebind to."""
    host = host_name(header)
    if not host:
        return False
    if host == "localhost":
        return True
    if ip(host) is not None:
        return is_local(host)
    if "." not in host:  # a bare machine name, e.g. "dmitriis-macbook-air"
        return bool(host) and all(c.isalnum() or c == "-" for c in host)
    return host.endswith(_LOCAL_SUFFIXES)


def allowed(peer, host_header, peers: list) -> str | None:
    """None when the request may proceed, else why not."""
    if not is_local(peer):
        return "this portal only answers this Mac, your local network and your tailnet"
    if not host_ok(host_header):
        return "unexpected Host header"
    if peers and not is_loopback(peer) and not any(ip(peer) == ip(p) for p in peers):
        return f"this device ({peer}) isn't on the allowed list. On the Mac, run: surfslop peers {peer}"
    return None


# --- the public portal and its stats page (PUBLIC.md) ----------------------------------------

def visitor(peer, forwarded: str | None) -> str:
    """Who a public request is from: Cloudflare's CF-Connecting-IP, trusted only when the request
    reached us from this Mac (the tunnel), else the peer. An IPv6 /64 is one visitor: a subscriber
    line gets a whole /64, so counting addresses would let one visitor be billions."""
    addr = (ip(forwarded) if forwarded and is_loopback(peer) else None) or ip(peer)
    if addr is None:
        return "unknown"
    return str(ipaddress.ip_network((addr, 64), strict=False)) if addr.version == 6 else str(addr)


def public_allowed(peer, host_header: str | None, hosts) -> str | None:
    """None when a request to the public portal may proceed. Only this Mac may connect (the tunnel
    runs here), and the Host must be one of your public names or a local one: otherwise a web page
    open in a browser on this Mac could rebind its own domain to 127.0.0.1 and read the portal."""
    if not is_loopback(peer):
        return "the public portal only answers the tunnel on this Mac"
    if host_name(host_header) in {h.lower() for h in hosts} or host_ok(host_header):
        return None
    return "unexpected Host header"


def stats_allowed(peer, headers) -> str | None:
    """None when a request to the stats page may proceed: tailnet or this Mac, never the tunnel."""
    addr = ip(peer)
    if addr is None or not (addr.is_loopback or addr in TAILNET or addr in TAILNET_V6):
        return "the stats page only answers your tailnet"
    if any(headers.get(h) for h in TUNNEL_HEADERS):
        return "the stats page is not served through the tunnel"
    if not host_ok(headers.get("Host")):
        return "unexpected Host header"
    return None


# --- Tailscale addresses ---------------------------------------------------------------------

def _cli() -> str | None:
    for name in _CLI:
        path = shutil.which(name) or (name if Path(name).is_file() else None)
        if path:
            return path
    return None


def _cli_ipv4(*args: str) -> str | None:
    cli = _cli()
    if not cli:
        return None
    try:
        out = subprocess.run([cli, "ip", "-4", *args], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return next((w for w in out.split() if ip(w) in TAILNET), None)


def tailscale_ip() -> str | None:
    """This Mac's Tailscale IPv4 address, or None if Tailscale isn't up."""
    address = _cli_ipv4()
    if address:
        return address
    try:  # no CLI: ask the OS which address it would use to reach Tailscale's DNS (no packet is sent)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("100.100.100.100", 53))
            address = s.getsockname()[0]
    except OSError:
        return None
    return address if ip(address) in TAILNET else None


def device_ip(name_or_ip: str) -> str:
    """A tailnet device's address from its IP or name ("muse"). Raises ValueError if unknown."""
    value = name_or_ip.strip()
    addr = ip(value)
    if addr is not None:
        if not is_local(value):
            raise ValueError(f"{value} isn't a private or Tailscale address")
        return str(addr)
    address = _cli_ipv4(value)
    if address:
        return address
    try:
        address = socket.gethostbyname(value)  # MagicDNS
    except OSError:
        address = ""
    if ip(address) in TAILNET:
        return address
    raise ValueError(f'Can\'t find a tailnet device called "{value}". Use its IP from `tailscale status`.')
