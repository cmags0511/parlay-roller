"""Pull DraftKings lines for every other sport ESPN carries into extra_lines.json.

Two kinds of bets, both from ESPN's free public feed (not an official API, so it can change):
  games  moneyline, spread and total with real prices, for every league that ESPN shows DraftKings odds for
  props  DraftKings player prop lines (line only, no price) for leagues that post them, e.g. MLB and NHL

NFL, NBA and college football stay in lines.json (fetch_lines.py) because the app has game logs for them.
If a league fails, its previous data is kept. Leagues with nothing posted are simply empty.

Usage: python fetch_extra.py [extra_lines.json]
"""
import json, os, re, sys, time, datetime as dt
import urllib.request, urllib.error

OUT = sys.argv[1] if len(sys.argv) > 1 else "extra_lines.json"
CACHE = "extra_athletes.json"  # athlete id -> [name, team abbr, position], so each player is looked up once
DK = "100"  # ESPN's provider id for DraftKings
MAX_NEW_ATHLETES = int(os.environ.get("EXTRA_MAX_ATHLETES") or 600)

# key: (label, sport, league, days ahead, try player props?)
LEAGUES = {
    "NFL": ("NFL", "football", "nfl", 7, False),
    "CFB": ("College football", "football", "college-football", 3, False),
    "NBA": ("NBA", "basketball", "nba", 3, False),
    "WNBA": ("WNBA", "basketball", "wnba", 3, True),
    "NCAAM": ("College basketball (M)", "basketball", "mens-college-basketball", 3, True),
    "NCAAW": ("College basketball (W)", "basketball", "womens-college-basketball", 3, False),
    "MLB": ("MLB", "baseball", "mlb", 3, True),
    "NHL": ("NHL", "hockey", "nhl", 3, True),
    "EPL": ("Premier League", "soccer", "eng.1", 5, True),
    "LALIGA": ("La Liga", "soccer", "esp.1", 5, True),
    "BUNDES": ("Bundesliga", "soccer", "ger.1", 5, True),
    "SERIEA": ("Serie A", "soccer", "ita.1", 5, True),
    "LIGUE1": ("Ligue 1", "soccer", "fra.1", 5, True),
    "MLS": ("MLS", "soccer", "usa.1", 5, True),
    "UCL": ("Champions League", "soccer", "uefa.champions", 7, True),
    "UEL": ("Europa League", "soccer", "uefa.europa", 7, True),
    "UFC": ("UFC", "mma", "ufc", 10, False),
    "ATP": ("ATP tennis", "tennis", "atp", 2, False),
    "WTA": ("WTA tennis", "tennis", "wta", 2, False),
    "PGA": ("PGA Tour", "golf", "pga", 3, False),
    "F1": ("Formula 1", "racing", "f1", 7, False),
    "NASCAR": ("NASCAR", "racing", "nascar-premier", 7, False),
}
# NFL, NBA and CFB game bets are included here; their player props live in lines.json.
SKIP = re.compile(r"half|quarter|period|inning|\b1st\b|\b2nd\b|first|longest|double|triple|or more|milestone|alt|to record|to hit|to score|anytime|race to|method|parlay")


def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (parlay-roller)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404 or i == tries - 1:
                raise
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def num(x):
    """'+118', '-143', 'o7.5', 7.5, 'EVEN' -> number (or None)."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return x
    s = str(x).strip().lower()
    if s in ("even", "ev", "pk", "pick"):
        return 100 if s in ("even", "ev") else 0
    m = re.search(r"[-+−]?\d+(?:\.\d+)?", s)
    if not m:
        return None
    v = float(m.group(0).replace("−", "-"))
    return int(v) if v.is_integer() else v


def pick(d, *path):
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def side_val(block, side, field):
    """Closing value for one side of a market, falling back to the opening one."""
    for state in ("close", "current", "open"):
        v = num(pick(block, side, state, field))
        if v is not None:
            return v
    return None


def draftkings_odds(comp):
    for o in comp.get("odds") or []:
        prov = o.get("provider") or {}
        if str(prov.get("id")) == DK or "draft" in str(prov.get("name", "")).lower():
            return o
    return None


def parse_game_odds(o):
    """ESPN odds block -> {'ml': {...}, 'spread': {...}, 'total': {...}} with only what is posted."""
    out = {}
    ml = {s: side_val(o.get("moneyline"), s, "odds") for s in ("away", "home")}
    if ml["away"] is None and ml["home"] is None:  # older shape
        ml = {"away": num(pick(o, "awayTeamOdds", "moneyLine")), "home": num(pick(o, "homeTeamOdds", "moneyLine"))}
    if ml["away"] is not None and ml["home"] is not None:
        out["ml"] = ml
    ps = o.get("pointSpread")
    sp = {}
    for s in ("away", "home"):
        line, price = side_val(ps, s, "line"), side_val(ps, s, "odds")
        if line is not None:
            sp[s] = {"line": line, "price": price}
    if len(sp) < 2 and num(o.get("spread")) is not None:  # older shape: one signed home spread
        h = num(o["spread"])
        sp = {"home": {"line": h, "price": sp.get("home", {}).get("price")}, "away": {"line": -h, "price": sp.get("away", {}).get("price")}}
    if len(sp) == 2:
        out["spread"] = sp
    tot = o.get("total")
    line = side_val(tot, "over", "line")
    if line is None:
        line = num(o.get("overUnder"))
    if line is not None:
        out["total"] = {"line": line, "over": side_val(tot, "over", "odds"), "under": side_val(tot, "under", "odds")}
    return out


def comp_to_game(ev, comp):
    cs = comp.get("competitors") or []
    if len(cs) != 2:
        return None
    home = next((c for c in cs if c.get("homeAway") == "home"), cs[0])
    away = next((c for c in cs if c is not home), cs[1])

    def nm(c):
        t, a = c.get("team") or {}, c.get("athlete") or {}
        full = t.get("displayName") or a.get("displayName") or a.get("fullName") or t.get("name") or ""
        ab = t.get("abbreviation") or a.get("shortName") or (full.split()[-1] if full else "?")
        return ab, full
    (ha, hn), (aa, an) = nm(home), nm(away)
    o = draftkings_odds(comp)
    return {"id": str(comp.get("id") or ev.get("id")), "ev": str(ev.get("id")), "t": comp.get("date") or ev.get("date"),
            "away": aa, "home": ha, "awayName": an, "homeName": hn, "odds": parse_game_odds(o) if o else {}}


def competitions(ev):
    comps = list(ev.get("competitions") or [])
    for g in ev.get("groupings") or []:  # tennis lists matches under groupings
        comps += g.get("competitions") or []
    return comps


def prop_label(name):
    n = re.sub(r"\(.*?\)", "", name).strip()
    n = re.sub(r"^total\s+", "", n, flags=re.I).strip()
    return n[:1].upper() + n[1:] if n else n


def load_json(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def main():
    now = dt.datetime.now(dt.timezone.utc)
    old = load_json(OUT, {})
    cache = load_json(CACHE, {})
    out = {"fetched": now.isoformat(timespec="minutes"), "book": "DraftKings", "leagues": {}, "log": []}
    log = out["log"].append
    new_lookups = 0
    any_ok = False

    def athlete(lg, sport, league, aid, teams):
        nonlocal new_lookups
        k = f"{lg}|{aid}"
        if k in cache:
            return cache[k]
        if new_lookups >= MAX_NEW_ATHLETES:
            return None
        new_lookups += 1
        try:
            a = get(f"https://sports.core.api.espn.com/v2/sports/{sport}/leagues/{league}/athletes/{aid}?lang=en&region=us", tries=2)
        except Exception:
            return None
        tm = re.search(r"/teams/(\d+)", pick(a, "team", "$ref") or "")
        cache[k] = [a.get("displayName") or a.get("fullName") or "", teams.get(tm.group(1), "") if tm else "", pick(a, "position", "abbreviation") or ""]
        time.sleep(0.1)
        return cache[k]

    for lg, (label, sport, league, days, want_props) in LEAGUES.items():
        base = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}"
        start = now.astimezone(dt.timezone(dt.timedelta(hours=-5))).date()
        events, seen = [], set()
        try:
            for k in range(days + 1):
                day = get(f"{base}/scoreboard?dates={start + dt.timedelta(days=k):%Y%m%d}" + ("&groups=80&limit=400" if league == "college-football" else ""))
                for e in day.get("events", []):
                    if e["id"] not in seen:
                        seen.add(e["id"]); events.append(e)
        except Exception as e:
            print(f"{lg}: scoreboard failed ({e}); keeping previous data"); log(f"{lg} scoreboard: {e}")
            if lg in (old.get("leagues") or {}):
                out["leagues"][lg] = old["leagues"][lg]
            continue
        any_ok = True
        games, props = [], []
        teams = {}
        for ev in events:
            if ev.get("status", {}).get("type", {}).get("state") != "pre":
                continue
            for comp in competitions(ev):
                if comp.get("status", {}).get("type", {}).get("state", "pre") != "pre":
                    continue
                for c in comp.get("competitors") or []:
                    t = c.get("team") or {}
                    if t.get("id") and t.get("abbreviation"):
                        teams[str(t["id"])] = t["abbreviation"]
                g = comp_to_game(ev, comp)
                if g and g["odds"]:
                    games.append(g)
        if want_props:
            for ev in events:
                if ev.get("status", {}).get("type", {}).get("state") != "pre":
                    continue
                comp = (ev.get("competitions") or [{}])[0]
                gid = str(comp.get("id") or ev["id"])
                gm = comp_to_game(ev, comp) or {}
                url = f"https://sports.core.api.espn.com/v2/sports/{sport}/leagues/{league}/events/{ev['id']}/competitions/{gid}/odds/{DK}/propBets"
                items, page, pages = [], 1, 1
                try:
                    while page <= pages:
                        d = get(f"{url}?limit=1000&page={page}")
                        items += d.get("items", [])
                        pages = d.get("pageCount", 1) or 1
                        page += 1
                except urllib.error.HTTPError as e:
                    if e.code != 404:
                        log(f"{lg} {ev.get('shortName')}: props {e.code}")
                    continue
                except Exception as e:
                    log(f"{lg} {ev.get('shortName')}: props {e}")
                    continue
                best = {}
                for it in items:
                    tname = (it.get("type") or {}).get("name", "")
                    cur = pick(it, "current", "target", "value")
                    m = re.search(r"/athletes/(\d+)", pick(it, "athlete", "$ref") or "")
                    if not m or cur is None or not tname or SKIP.search(tname.lower()):
                        continue
                    key = (m.group(1), tname)
                    upd = it.get("lastUpdated", "")
                    if key not in best or upd > best[key][0]:
                        best[key] = (upd, cur, pick(it, "open", "target", "value"), tname)
                for (aid, _), (upd, cur, opn, tname) in best.items():
                    who = athlete(lg, sport, league, aid, teams)
                    if not who or not who[0]:
                        continue
                    props.append({"gid": gid, "t": gm.get("t"), "away": gm.get("away"), "home": gm.get("home"),
                                  "aid": aid, "name": who[0], "team": who[1], "pos": who[2],
                                  "label": prop_label(tname), "line": cur, "open": opn, "u": upd})
                time.sleep(0.2)
        props = props[:8000]
        out["leagues"][lg] = {"name": label, "sport": sport, "games": games, "props": props}
        print(f"{lg}: {len(games)} games with DK odds, {len(props)} player props")
        log(f"{lg}: {len(events)} on scoreboard, {len(games)} with odds, {len(props)} props")
    if not any_ok:
        print("ESPN unreachable; keeping previous extra_lines.json")
        old["log"] = out["log"]
        out = old if old else out
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    json.dump(cache, open(CACHE, "w"), separators=(",", ":"))
    print(f"wrote {OUT} ({new_lookups} new athlete lookups)")


if __name__ == "__main__":
    main()
