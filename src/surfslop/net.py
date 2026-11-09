"""One polite HTTP client for every source.

* It says who it is (``User-Agent: surfslop/…``) and never pretends to be a browser.
* Every response is cached on disk for the source's ``ttl``; when a refresh fails, the last
  copy is served for up to a day, so a source that is down doesn't blank the page.
* One request at a time per host, a short pause between them.
* A host that answers 401/403/429 is left alone for a while (``BACKOFF``). There is no retry
  with other headers or another client: a refusal is taken as the answer. The refusal is
  written to disk, so restarting the portal or running the CLI doesn't ask again early.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from . import __version__, config

USER_AGENT = f"surfslop/{__version__} (personal surf forecast portal; github.com/duman190/surfslop)"
MAX_STALE = 24 * 3600
BACKOFF = {401: 6 * 3600, 403: 6 * 3600, 429: 3600}
MIN_GAP = 0.35  # seconds between two requests to one host

_host_locks: dict = {}
_host_last: dict = {}
_url_locks: dict = {}
_guard = threading.Lock()


class FetchError(Exception):
    pass


def _lock(table: dict, key: str) -> threading.Lock:
    with _guard:
        return table.setdefault(key, threading.Lock())


def _cache_file(url: str):
    return config.cache_dir("http") / (hashlib.sha1(url.encode()).hexdigest() + ".bin")


def _blocks() -> dict:
    try:
        return json.loads((config.cache_dir() / "blocked.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def blocked(host: str) -> str | None:
    """Why a host is being left alone right now, or None."""
    until, reason = _blocks().get(host, (0, ""))
    return reason if until > time.time() else None


def blocked_until(host: str) -> float | None:
    """When a host that refused us may be asked again, or None if it isn't being left alone."""
    until = _blocks().get(host, (0, ""))[0]
    return until if until > time.time() else None


def block(host: str, seconds: float, why: str) -> str:
    until = time.time() + seconds
    reason = f"{why}; not asking again until {time.strftime('%a %-I:%M %p', time.localtime(until))}"
    config.write_private(config.cache_dir() / "blocked.json", json.dumps({**_blocks(), host: [until, reason]}))
    return reason


def cached(url: str, max_age: float = MAX_STALE) -> tuple[bytes, float] | None:
    """The copy on disk and when it was fetched, without touching the network."""
    path = _cache_file(url)
    if not path.exists() or time.time() - path.stat().st_mtime > max_age:
        return None
    return path.read_bytes(), path.stat().st_mtime


def _download(url: str, headers: dict | None, timeout: float) -> bytes:
    host = urlsplit(url).netloc
    reason = blocked(host)
    if reason:
        raise FetchError(reason)
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept-Encoding": "gzip", **(headers or {})})
    with _lock(_host_locks, host):
        wait = _host_last.get(host, 0) + MIN_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as e:
            if e.code in BACKOFF:
                raise FetchError(block(host, BACKOFF[e.code], f"{host} answered {e.code}")) from e
            raise FetchError(f"{host} answered {e.code}") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise FetchError(f"{host}: {getattr(e, 'reason', e)}") from e
        finally:
            _host_last[host] = time.time()


def get(url: str, *, ttl: float, headers: dict | None = None, timeout: float = 25,
        stale_ok: bool = True) -> tuple[bytes, float]:
    """(body, when it was fetched). Raises FetchError when there is no usable copy, or, with
    `stale_ok=False`, whenever the refresh itself fails."""
    path = _cache_file(url)
    with _lock(_url_locks, url):
        age = time.time() - path.stat().st_mtime if path.exists() else None
        if age is not None and age < ttl:
            return path.read_bytes(), path.stat().st_mtime
        try:
            body = _download(url, headers, timeout)
        except FetchError:
            if stale_ok and age is not None and age < MAX_STALE:
                return path.read_bytes(), path.stat().st_mtime
            raise
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        return body, time.time()


def get_json(url: str, **kwargs) -> tuple[object, float]:
    body, fetched = get(url, **kwargs)
    try:
        return json.loads(body), fetched
    except (ValueError, UnicodeDecodeError) as e:
        _cache_file(url).unlink(missing_ok=True)
        raise FetchError(f"{urlsplit(url).netloc} sent something that isn't JSON") from e
