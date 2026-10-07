"""Build cfb_data.json (college football, every FBS team) from ESPN's public box scores.

ESPN's site API has full box scores for every FBS game: passing, rushing, receiving,
defense and kicking. Each finished game is fetched once and kept in cfb_games.json.gz,
so later runs only download new games. Like the other ESPN feeds this is not an official
API; if ESPN can't be reached, the existing files are left alone.

Output has the same columns as the NFL data so the app can reuse its NFL props.

Usage: python build_cfb.py [cfb_data.json]
"""
import gzip, json, os, re, sys, time, datetime as dt
import urllib.request, urllib.error

OUT = sys.argv[1] if len(sys.argv) > 1 else "cfb_data.json"
CACHE = "cfb_games.json.gz"
B = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
H = {"User-Agent": "Mozilla/5.0 (parlay-roller)"}
NOW = dt.datetime.now(dt.timezone.utc)
TODAY = NOW.astimezone(dt.timezone(dt.timedelta(hours=-4))).date()
CUR = TODAY.year if TODAY.month >= 7 else TODAY.year - 1  # season = year it starts
COLS = ["date", "week", "po", "opp", "home", "spread", "total", "cmp", "att", "pyd", "ptd",
        "car", "ryd", "rec", "recyd", "td", "fgm", "pat", "tkl", "sck", "dint"]
STAT = ["cmp", "att", "pyd", "ptd", "car", "ryd", "rtd", "rec", "recyd", "rectd", "tkl", "sck", "dint", "fgm", "pat"]
calls = 0


def get(url, tries=3):
    global calls
    for i in range(tries):
        try:
            calls += 1
            with urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404 or i == tries - 1:
                raise
        except Exception:
            if i == tries - 1:
                raise
        time.sleep(2 * (i + 1))


def num(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def spread_total(odds, home, away):
    """ESPN odds 'details' look like 'MIZ -3.5' (favorite and line). Return (home spread, total)."""
    if not odds:
        return None, None
    o = odds[0] if isinstance(odds, list) else odds
    tot = o.get("overUnder")
    det = (o.get("details") or "").strip()
    sp = None
    if det.upper() in ("EVEN", "PK", "PICK"):
        sp = 0.0
    else:
        m = re.match(r"^(\S+)\s+([+-]?\d+(?:\.\d+)?)$", det)
        if m:
            fav, val = m.group(1), abs(float(m.group(2)))
            sp = -val if fav == home else val if fav == away else None
    return sp, (float(tot) if tot not in (None, "") else None)


def parse_summary(s):
    """Per-player stat lines from one finished game."""
    players = {}
    for t in s.get("boxscore", {}).get("players", []):
        team = t["team"]["abbreviation"]
        for cat in t.get("statistics", []):
            keys = cat.get("keys") or []
            for a in cat.get("athletes") or []:
                ath = a.get("athlete") or {}
                aid = ath.get("id")
                if not aid or aid.startswith("-"):
                    continue
                st = dict(zip(keys, a.get("stats") or []))
                p = players.setdefault(aid, {"n": ath.get("displayName", "").strip(), "t": team, **{k: 0 for k in STAT}})
                name = cat.get("name")
                if name == "passing":
                    c = (st.get("completions/passingAttempts") or "0/0").split("/")
                    p["cmp"], p["att"] = num(c[0]), num(c[1] if len(c) > 1 else 0)
                    p["pyd"], p["ptd"] = num(st.get("passingYards")), num(st.get("passingTouchdowns"))
                elif name == "rushing":
                    p["car"], p["ryd"], p["rtd"] = num(st.get("rushingAttempts")), num(st.get("rushingYards")), num(st.get("rushingTouchdowns"))
                elif name == "receiving":
                    p["rec"], p["recyd"], p["rectd"] = num(st.get("receptions")), num(st.get("receivingYards")), num(st.get("receivingTouchdowns"))
                elif name == "defensive":
                    p["tkl"], p["sck"] = num(st.get("totalTackles")), num(st.get("sacks"))
                elif name == "interceptions":
                    p["dint"] = num(st.get("interceptions"))
                elif name == "kicking":
                    fg = (st.get("fieldGoalsMade/fieldGoalAttempts") or "0/0").split("/")
                    xp = (st.get("extraPointsMade/extraPointAttempts") or "0/0").split("/")
                    p["fgm"], p["pat"] = num(fg[0]), num(xp[0])
    return {aid: [p["n"], p["t"]] + [int(p[k]) if k != "sck" else p[k] for k in STAT] for aid, p in players.items()}


def week_events(season, stype, week):
    try:
        d = get(f"{B}/scoreboard?groups=80&dates={season}&seasontype={stype}&week={week}&limit=400")
    except Exception as e:
        print(f"scoreboard {season} t{stype} w{week} failed: {e}")
        return None
    return d.get("events", [])


def main():
    try:
        cache = json.load(gzip.open(CACHE, "rt"))
    except Exception:
        cache = {"games": {}, "closed": []}
    games, closed = cache["games"], set(cache.get("closed", []))
    new = 0
    reached = False
    for season in (CUR - 1, CUR):
        for stype, weeks in ((2, range(1, 17)), (3, range(1, 2))):
            for wk in weeks:
                key = f"{season}-{stype}-{wk}"
                if key in closed:
                    continue
                evs = week_events(season, stype, wk)
                if evs is None:
                    continue
                reached = True
                all_final = bool(evs)
                for e in evs:
                    st = e["status"]["type"]
                    if not st.get("completed"):
                        all_final = all_final and st.get("state") == "post"
                        continue
                    if e["id"] in games:
                        continue
                    try:
                        s = get(f"{B}/summary?event={e['id']}")
                    except Exception as ex:
                        print(f"summary {e['id']} failed: {ex}")
                        all_final = False
                        continue
                    comp = e["competitions"][0]
                    tm = {c["homeAway"]: c["team"]["abbreviation"] for c in comp["competitors"]}
                    sp, tot = spread_total(s.get("pickcenter") or comp.get("odds"), tm.get("home"), tm.get("away"))
                    games[e["id"]] = {"d": e["date"][:10], "wk": wk, "po": int(stype == 3), "h": tm.get("home"),
                                      "a": tm.get("away"), "sp": sp, "tot": tot, "p": parse_summary(s)}
                    new += 1
                    time.sleep(0.05)
                # a week from a finished season, or more than 10 days old, never changes again
                last = max((e["date"][:10] for e in evs), default="")
                if all_final and (season < CUR or (last and dt.date.fromisoformat(last) < TODAY - dt.timedelta(days=10))):
                    closed.add(key)
    if not reached:
        print("ESPN unreachable; keeping existing college data")
        return
    cache = {"games": games, "closed": sorted(closed)}
    with gzip.open(CACHE, "wt") as f:
        json.dump(cache, f, separators=(",", ":"))

    # ---- player game logs ----
    logs = {}
    for gid, g in games.items():
        for aid, row in g["p"].items():
            name, team, *vals = row
            s = dict(zip(STAT, vals))
            home = team == g["h"]
            opp = g["a"] if home else g["h"]
            sp = g["sp"] if home else (None if g["sp"] is None else -g["sp"])
            logs.setdefault(aid, []).append((g["d"], team, name, [
                g["d"], g["wk"], g["po"], opp, int(home), sp, g["tot"],
                s["cmp"], s["att"], s["pyd"], s["ptd"], s["car"], s["ryd"], s["rec"], s["recyd"],
                s["rtd"] + s["rectd"], s["fgm"], s["pat"], s["tkl"], s["sck"], s["dint"]]))
    cur_start = f"{CUR}-07-01"
    players = []
    for aid, rows in logs.items():
        rows.sort(key=lambda r: r[0])
        cur = [r[3] for r in rows if r[0] >= cur_start]
        if len(cur) < 2:
            continue  # only players active this season
        R = [r[3] for r in rows]
        mean = lambda i: sum(r[i] for r in cur) / len(cur)
        att, car, rec, recyd = mean(8), mean(11), mean(13), mean(14)
        fg, xp, tkl, sck = mean(16), mean(17), mean(18), mean(19)
        if att >= 10:
            pos = "QB"
        elif fg + xp >= 1 and att + car + rec < 1:
            pos = "K"
        elif car >= 5 and car >= rec:
            pos = "RB"
        elif rec >= 1.5 or recyd >= 20:
            pos = "WR"
        elif tkl >= 3.5 or sck >= .4:
            pos = "DEF"
        else:
            continue
        players.append({"id": aid, "e": aid, "n": rows[-1][2], "t": rows[-1][1], "p": pos, "g": R})

    # ---- upcoming games (next 9 days) ----
    up, seen = [], set()
    for k in range(10):
        day = TODAY + dt.timedelta(days=k)
        try:
            d = get(f"{B}/scoreboard?groups=80&dates={day:%Y%m%d}&limit=400")
        except Exception:
            continue
        for e in d.get("events", []):
            if e["id"] in seen or e["status"]["type"].get("state") != "pre":
                continue
            seen.add(e["id"])
            comp = e["competitions"][0]
            tm = {c["homeAway"]: c["team"]["abbreviation"] for c in comp["competitors"]}
            sp, tot = spread_total(comp.get("odds"), tm.get("home"), tm.get("away"))
            t = dt.datetime.fromisoformat(e["date"].replace("Z", "+00:00")).isoformat()
            up.append([t, tm.get("away"), tm.get("home"), sp, tot, (e.get("week") or {}).get("number")])
    up.sort(key=lambda u: u[0])

    out = {"built": NOW.isoformat(timespec="minutes"), "season": str(CUR), "curStart": cur_start,
           "cols": COLS, "players": players, "upcoming": up}
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    from collections import Counter
    print(f"CFB: {len(games)} games cached (+{new} new, {calls} requests), {len(players)} players "
          f"{dict(Counter(p['p'] for p in players))}, {len(up)} upcoming -> {OUT} ({os.path.getsize(OUT)//1024} KB)")


if __name__ == "__main__":
    main()
