---
name: surfslop
description: Bay Area surf forecasts (Capitola to Ocean Beach) from Dmitrii's Mac over Tailscale. A week per spot, fused from several forecasts, with each source's own numbers, wind, tides, buoys, a satellite picture with the swell drawn on it, and a ready-to-post forecast card. Use it for the weekly surf poll and any "how's the surf" question.
---

# surfslop

Dmitrii's MacBook runs surfslop, a read-only surf forecast portal. You reach it over
Tailscale. It replaces fetching Open-Meteo yourself: it already reads Open-Meteo's wave
models, the NWS marine forecast and surf-forecast.com (and Surfline, when Dmitrii has
switched it on), brings them to each break, and fuses them.

## Setup (once)

One setting, `SURFSLOP_URL`: the address `surfslop status` prints on the Mac, e.g.
`http://100.101.102.103:7873`. There is no token: the Mac knows you by your Tailscale
address. Calls from outside the tailnet get `403 forbidden`.

```sh
curl -s "$SURFSLOP_URL/v1/overview?format=md"                 # the seven poll spots, next 3 days
curl -s "$SURFSLOP_URL/v1/forecast/steamer-lane?format=md"    # one spot, the week
curl -s "$SURFSLOP_URL/v1/card.png?days=3" -o forecast.png    # the picture for the poll
```

Allow 30 seconds (the first call after a while fetches the sources), and 90 seconds with `ai=1`.

## Endpoints

| | |
|---|---|
| `GET /v1/overview` | The featured spots × the next days. `days=1..8` (default 3), `spots=all` for all 38, `format=md` for Markdown, `ai=1` adds a written "where to go". |
| `GET /v1/forecast/{spot}` | One spot: `days` (fused, with `by_source`, `periods`, `tides`, `confidence`), `now`, `buoys`, `sources`. `format=md` for Markdown; `hours=0` drops the hourly arrays; `ai=1` adds a written overview. |
| `GET /v1/spots?q=` | Spot ids and names. A name or nickname works wherever an id does: `ocean-beach`, `OB`, `linda mar`, `surfers beach`. |
| `GET /v1/satellite/{spot}.jpg?arrows=1` | Satellite picture, north up, with the swell (blue; purple for a second swell) and wind (yellow) drawn in. `t=EPOCH` picks the hour (default now). |
| `GET /v1/card.png?days=3` | The overview as a PNG: a row per spot, wave height per day, three pills for morning, midday, afternoon. |
| `GET /v1/status` | Which sources are up, which are switched off. |
| `GET /llms.txt` | This contract, short. |

## Reading the numbers

- **Surf** is feet of wave face at the break (`surf: "6-8"`, `surf_min`, `surf_max`), not the
  open-ocean swell. `swell` is the open-ocean swell it comes from ("9 ft @ 15 s WNW").
- **clean / fair / blown** is what the wind does to the surface, per `periods`: `morning`
  (6-10), `midday` (10-14), `afternoon` (14-18), Pacific time. `wind.type` says offshore,
  cross or onshore for that spot.
- **stars** (0-5) weighs size, swell period and wind; `best` names the day's best period.
- **confidence** (`high`, `medium`, `low`) is how well the sources agree. On a `low` day, say
  so and quote the range across `by_source` rather than one number.
- **by_source** holds each forecast's own height for the day, so you can say who says what.
- Days 1-5 have the most sources; days 6-8 are wave models only and less certain.

## Rules

1. **Don't invent numbers.** Everything you say about the surf comes from a response. If the
   Mac doesn't answer (connection refused or timeout), it's asleep or offline: tell Dmitrii
   and retry later; don't fall back to guessing.
2. **Waves over 4 ft are not for beginners; over 10 ft is experts only**, whatever the spot.
   Say so when the crew includes beginners.
3. **Name the source when they disagree.** "Surfline says 4-6, the wave models 6-8."
4. **`ai=1` text is a local model's summary of the same numbers.** Use it as a draft; the
   numbers win if they differ.
5. A source listed in `off` or with `ok: false` is simply missing from the fusion. Mention
   it only if Dmitrii asks why a number moved.

## Weekly surf poll

Friday morning, before posting with the telegram4muse skill:

1. `GET /v1/overview?format=json&days=3` (Fri, Sat, Sun) and `GET /v1/card.png?days=3`.
2. Pick the best windows from `stars`, `best` and `periods`; check `confidence`.
3. Post the poll, then reply with `card.png` as the photo. Caption: two or three lines, e.g.
   "**Sat** Steamer 6-8 ft, clean till noon. **Sun** smaller, 4-5 ft, light onshore by 2."
4. For the spot the crew picks, `GET /v1/satellite/{spot}.jpg?arrows=1&t=<epoch of the session>`
   makes a good follow-up picture.

Errors come back as `{"error": {"code", "message"}}`: `404 unknown_spot` (see `/v1/spots`),
`403 forbidden` (the message has the exact `surfslop peers ...` command for Dmitrii),
`502 source_unavailable`.
