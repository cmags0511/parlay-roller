# Parlay Roller

A phone-friendly web app that rolls a random parlay from live DraftKings lines across every sport.

- **Game bets** (moneyline, spread, total) with real DraftKings prices for every sport and league ESPN shows DraftKings odds for: football, basketball, baseball, hockey, soccer (hundreds of leagues), tennis, MMA/UFC, rugby, cricket, lacrosse, volleyball and more. The fetcher asks ESPN's catalog which leagues exist, so new ones appear on their own.
- **Player props** wherever ESPN posts them. NFL, NBA and college football props also show last-10 hit rates from game logs; other sports show the line and its movement.
- Pick styles: Chaos (pure random) or Lean on data. Filter by sport, league, bet type and game. Lock or re-roll any leg, set your stake or own odds, save and copy parlays.

Not covered: golf and racing outrights (no head-to-head matchup in ESPN's feed) and any sport ESPN doesn't carry. ESPN's feed is free but unofficial and can change. Not betting advice; parlays carry a built-in sportsbook edge.

## How it updates
`.github/workflows/update.yml` runs every 3 hours and commits fresh data:

| Script | Writes | What |
| --- | --- | --- |
| `fetch_lines.py` | `lines.json` | DK player props for NFL, NBA, college football |
| `fetch_history.py` | `extra_history.json` | Recent ESPN game logs for MLB / NHL / WNBA players with props, turned into the stat each prop is about (hits, strikeouts, points…). Soccer props (shots by foot or header) have no game-log equivalent |
| `fetch_extra.py` | `extra_lines.json`, `extra_athletes.json`, `extra_leagues.json` | DK game bets for every sport and props for other sports; remembers quiet leagues so it skips them for a while |
| `build_data.py` | `props_data.json` | NFL and NBA game logs and schedules (nflverse, sportsdataverse) |
| `build_cfb.py` | `cfb_data.json`, `cfb_games.json.gz` | College football box scores |

## Run it
Serve the folder over HTTP (the page fetches the JSON files): `python3 -m http.server`, then open `http://localhost:8000`.
Host it for free with GitHub Pages (Settings, Pages, deploy from the `main` branch root).
