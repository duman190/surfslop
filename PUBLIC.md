# Going public: `surfslop serve --public` behind a Cloudflare Tunnel

The same arrangement as the Lynch Pin portal: the portal listens on this Mac only, a Cloudflare
Tunnel is the one way in, no router port is opened, and a separate stats page is reachable from
the tailnet only.

```sh
surfslop serve --public --hostname surf.example.com   # public portal on 127.0.0.1:7874, stats on <tailscale-ip>:7875
cloudflared tunnel run surfslop                       # the tunnel (cloudflared.example.yml)
surfslop serve --tailnet                              # optional, alongside: your own full portal on 7873
```

Nothing here publishes anything by itself. The site exists only while both `--public` and
`cloudflared` run and a DNS record points your name at the tunnel; stopping `cloudflared` takes it
offline (the kill switch).

## What the public sees

**Only the open sources.** The public portal fuses the NWS marine forecast and Open-Meteo's wave
models, with NOAA tides and buoys and USGS imagery. It does not read, fuse or show Surfline or
surf-forecast.com: their forecasts, ratings and written notes are theirs to publish, not ours, so
they stay in the private portal. The per-spot tuning the private portal learned stays in use (it
is one number per spot), but is not shown.

The written overviews are separate too: the public ones are written from the open data only and
stored under their own names (`pub-…`), so a private overview can never be served to a visitor.

Everything else is the same page: search, the week at the seven spots, spot pages with the
satellite picture and the hourly chart, the forecast card, `/llms.txt`.

## Precautions in the code (tests: `tests/test_public.py`, `tests/test_server.py`)

| | |
|---|---|
| Listens on 127.0.0.1 only | `--public` can't be combined with `--tailnet` or `--lan`. The tunnel is the only way in. |
| Host names | Only the names you pass (`--hostname`, or `public_hosts` in config.json), plus `localhost` and private names. Without this a web page open in a browser on this Mac could rebind its own domain to 127.0.0.1 and read the portal. |
| Who the visitor is | Cloudflare's `CF-Connecting-IP`, trusted only when the request comes from loopback (the tunnel). An IPv6 /64 counts as one visitor. |
| Rate limit | Per visitor: one request a second, bursts of 60; a drawn picture counts as three. Past that, 429 with `Retry-After`. At most 64 requests are answered at once (503 past that). |
| The local model | A visitor can't make it write on demand: `ai=1` does nothing. An overview is written once per spot per day (the daily edition writes the seven home page spots at 4 AM); writing for visitors is capped at 40 an hour, 2 streams per visitor, 8 in all. One already written is always served. |
| Upstream sources | Visitors can't spend them either: every source response is cached for its hour, whatever the traffic. `refresh=1` does nothing. |
| Read-only | GET and HEAD. A request with a body gets 400 (405 for POST and the rest) and its connection is closed, so a body is never read as a second request on cloudflared's kept-alive connection. |
| Headers | `Strict-Transport-Security`, a strict Content-Security-Policy (`default-src 'self'`, no inline script or style), `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, `Cross-Origin-Resource-Policy: same-origin`. |
| Other sites | A page elsewhere may link to the portal but may not read its API through a visitor's browser (`Sec-Fetch-Site`). |
| What is served | The page's four files by name, and the API. No file paths are taken from the request. Tracebacks never reach a visitor; error messages name nothing on this Mac; `/v1/status` is trimmed. |
| Pictures | Pillow only decodes JPEG and PNG from the network (USGS tiles). |
| Logs and stats | Visitors appear as hashes, never as addresses. |

## The stats page

`http://<this Mac's Tailscale address>:7875` (also `127.0.0.1:7875`): visitors a day and a month,
requests a minute, spots looked up, refusals by reason, time to answer, overviews written.

It is a second server that listens on the Tailscale address only, answers Tailscale and loopback
callers only, and refuses any request that carries Cloudflare's headers, so it can't be reached
through the tunnel even by mistake (and the tunnel's config doesn't point at it). Not the LAN: on a
café Wi-Fi nobody else can open it. `--no-stats` leaves it out; `--stats-port` moves it.

Visitors are stored as keyed hashes (HMAC-SHA256 with a random key kept beside them), for 31 days;
after that only each day's count remains, for a year.

## Before the name resolves (one time)

1. **Python and packages.** Run the public portal on Python 3.13 (`.venv` here already is), with
   Pillow installed by hash: `requirements-public.txt`.
2. **Tunnel.** `brew install cloudflared`, `cloudflared tunnel login`, `cloudflared tunnel create
   surfslop`, then `cloudflared.example.yml` → `~/.cloudflared/config.yml` (mode 600, like the
   credentials file). `cloudflared tunnel ingress validate` should show your name → `127.0.0.1:7874`
   and everything else → 404. No IP routes, ever.
3. **Cloudflare dashboard**, before any DNS record exists: Always Use HTTPS; minimum TLS 1.2; a WAF
   rule that blocks every method but GET and HEAD; a rate-limit rule on `/v1/` (for example 100
   per 10 s per IP); DNSSEC. Email anti-spoofing records if the domain sends no mail.
4. **Soft launch.** Put Cloudflare Access in front of the name (only your email), then
   `cloudflared tunnel route dns surfslop surf.example.com` and try it from a phone on cellular.
   Check the stats page counts you as one visitor and that `/v1/status` shows no private source.
   Remove Access when you want it open; there is no login in the portal itself.

## Kill switch

Stop `cloudflared` (Ctrl-C, or `cloudflared service uninstall`): the name stops answering at once.
Deleting the DNS record makes it stop resolving. Stopping `surfslop serve --public` leaves the
tunnel answering 502.

## Keeping it patched

Monthly, and on any advisory: `brew upgrade cloudflared`, macOS updates, and Pillow (re-pin its
hash in `requirements-public.txt`). The page has no npm packages and loads nothing from other sites.
