"""The written overview, by a local model (inference-switchboard, OpenAI-compatible chat API).

The model gets the fused numbers and every source's own numbers as a terse block (text.py) and
writes three short paragraphs. One request at a time: the 27B serves one. An overview is written
once per edition (config.edition_start: each morning at 4) and kept for the day; it is written
again only if the set of sources behind it changes. The daily edition (daily.py) writes the home
page's spots ahead of time. Public overviews (`--public`) are written from the open sources only
and stored under their own names, so a private overview can never be served to the public.
With no model reachable the page simply has no overview.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request

from . import config, net, text

SPOT_SYSTEM = """You are a Bay Area surf forecaster. A surfer will read your note to decide when to paddle out at one spot this week.
You are given the fused forecast, each source's own numbers, and, when available, the written forecasts of Surfline's forecaster and of surf-forecast.com. Combine all of it into one overview: lean on the forecaster for the next day or two, on the fused numbers for the rest of the week. Write in your own words; never quote the written forecasts.
Write exactly three short paragraphs, each starting with its bold label:
**Bottom line:** the week in one or two sentences: size trend and the best day.
**Best windows:** two or three specific day-and-time windows, with the wave size, wind and tide that make them good.
**Heads up:** where the sources disagree, in numbers or in their written forecasts (name them), low-confidence days, and anything unsafe or too big for this spot.
Rules: use only the data given; feet for wave size; waves over 4 ft are not for beginners and over 10 ft are for experts only, whatever the spot; no preamble, no lists, no closing line; under 150 words in total."""

REGION_SYSTEM = """You are a Bay Area surf forecaster. Surfers will read your note to pick where and when to surf over the next days, between Santa Cruz and San Francisco.
Write exactly two short paragraphs, each starting with its bold label:
**Where to go:** the best spot-and-day combinations, with wave size and time of day; say which spots suit beginners and which are for experienced surfers on the bigger days.
**Skip:** spots or days that are too big, blown out or uncertain.
Rules: use only the data given; feet for wave size; waves over 4 ft are not for beginners and over 10 ft are for experts only, whatever the spot; no preamble, no lists, no closing line; under 110 words in total."""

_lock = threading.Lock()  # one generation at a time
_model: dict = {}


class Unavailable(Exception):
    pass


def _model_id(base: str) -> str:
    if base not in _model:
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{base}/v1/models", headers={"User-Agent": net.USER_AGENT}),
                                        timeout=5) as r:
                _model[base] = json.load(r)["data"][0]["id"]
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as e:
            raise Unavailable(f"the local model at {base} isn't answering") from e
    return _model[base]


def _cache_path(name: str):
    return config.cache_dir("ai") / f"{name}.json"


def cached(name: str, system: str, shape: str = "", since: float = 0.0) -> dict | None:
    """The stored overview, if it was written to the same instructions, from the same sources
    (`shape`), in the current edition (`since`)."""
    try:
        entry = json.loads(_cache_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if entry.get("system") != _digest(system) or entry.get("shape", "") != shape:
        return None  # the instructions or the sources changed: write a new one
    if entry.get("generated", 0) < since:
        return None  # yesterday's
    return entry


def _digest(words: str) -> str:
    return hashlib.sha1(words.encode()).hexdigest()


def stream(name: str, system: str, block: str, cfg: config.Config, shape: str = ""):
    """Yields the overview piece by piece, then stores it. Raises Unavailable before the first piece."""
    since = config.edition_start(cfg.daily_at)
    hit = cached(name, system, shape, since)
    if hit:
        yield hit["text"]
        return
    if not cfg.llm_url:
        raise Unavailable("the AI overview is switched off (llm_url is empty)")
    base = cfg.llm_url.rstrip("/")
    body = json.dumps({
        "model": _model_id(base), "stream": True, "temperature": 0.3, "max_tokens": cfg.llm_max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": block}],
    }).encode()
    request = urllib.request.Request(f"{base}/v1/chat/completions", data=body, headers={
        "Content-Type": "application/json", "User-Agent": net.USER_AGENT})
    with _lock:
        hit = cached(name, system, shape, since)  # someone else may have written it while we waited
        if hit:
            yield hit["text"]
            return
        started, parts = time.time(), []
        try:
            response = urllib.request.urlopen(request, timeout=180)
        except (urllib.error.URLError, OSError) as e:
            _model.pop(base, None)
            raise Unavailable(f"the local model didn't answer ({getattr(e, 'reason', e)})") from e
        with response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:") or line == "data: [DONE]":
                    continue
                try:
                    delta = json.loads(line[5:])["choices"][0].get("delta", {}).get("content")
                except (ValueError, KeyError, IndexError):
                    continue
                if delta:
                    parts.append(delta)
                    yield delta
        written = "".join(parts).strip()
        if written:
            config.write_private(_cache_path(name), json.dumps({
                "system": _digest(system), "shape": shape, "text": written, "model": _model.get(base),
                "generated": int(time.time()),
                "seconds": round(time.time() - started, 1)}))


def _shape(data: dict) -> str:
    """Which sources stand behind the numbers, and how many written forecasts came with them."""
    return ",".join(s["id"] for s in data["sources"] if s["ok"]) + f"|{len(data.get('writeups', []))}"


def _name(data: dict, name: str) -> str:
    return ("pub-" if data.get("public") else "") + name


def spot_stream(data: dict, cfg: config.Config):
    return stream(_name(data, data["spot"]["id"]), SPOT_SYSTEM, text.ai_block(data), cfg, _shape(data))


def region_stream(overview: dict, cfg: config.Config):
    return stream(_name(overview, "_region"), REGION_SYSTEM, text.region_block(overview), cfg, _shape(overview))


def spot_cached(data: dict, cfg: config.Config | None = None) -> dict | None:
    since = config.edition_start((cfg or config.load()).daily_at)
    return cached(_name(data, data["spot"]["id"]), SPOT_SYSTEM, _shape(data), since)


def region_cached(overview: dict, cfg: config.Config | None = None) -> dict | None:
    since = config.edition_start((cfg or config.load()).daily_at)
    return cached(_name(overview, "_region"), REGION_SYSTEM, _shape(overview), since)


def write(generator) -> str | None:
    """Run a stream to the end; None when the model is unavailable."""
    try:
        return "".join(generator).strip() or None
    except Unavailable:
        return None
