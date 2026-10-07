# Parlay Roller

A phone-friendly web app that rolls a random parlay from live DraftKings lines across every sport.

- **Game bets** (moneyline, spread, total) with real DraftKings prices for NFL, NBA, college football and basketball, WNBA, MLB, NHL, soccer, UFC, tennis, golf and racing, wherever ESPN posts them.
- **Player props** for every sport ESPN carries them for. NFL, NBA and college football props also show last-10 hit rates from game logs.
- Pick styles: Chaos (pure random), Lean on data, Top picks. Lock or re-roll any leg, set stake or your own odds, save and copy parlays.

Lines come from ESPN's public feed, which is not an official API and can change. Not betting advice; parlays carry a built-in sportsbook edge.

## How it updates
`.github/workflows/update.yml` runs every 3 hours and commits fresh data:

| Script | Writes | What |
| --- | --- | --- |
| `fetch_lines.py` | `lines.json` | DK player props for NFL, NBA, college football |
| `fetch_extra.py` | `extra_lines.json`, `extra_athletes.json` | DK game bets for all sports and props for other sports |
| `build_data.py` | `props_data.json` | NFL and NBA game logs and schedules (nflverse, sportsdataverse) |
| `build_cfb.py` | `cfb_data.json`, `cfb_games.json.gz` | College football box scores |
| `picks.py` | `picks.json` | Ranked top picks |

## Run it
Serve the folder over HTTP (the page fetches the JSON files): `python3 -m http.server`, then open `http://localhost:8000`.
Host it for free with GitHub Pages (Settings, Pages, deploy from the `main` branch root).
