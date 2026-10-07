"""Pull DraftKings lines for every sport and league ESPN carries into extra_lines.json.

It asks ESPN's own catalog which sports and leagues exist (football, basketball, baseball, hockey,
soccer with hundreds of leagues, tennis, MMA, golf, racing, rugby, cricket, lacrosse, volleyball and
more), then keeps every upcoming game or match that ESPN shows DraftKings odds for.

  games  moneyline, spread and total with real prices (two-sided matchups: teams, fighters, tennis players)
  props  DraftKings player prop lines (line only, no price) for leagues that post them

NFL, NBA and college football player props come from fetch_lines.py (the app has game logs for those).
Leagues with nothing upcoming are remembered in extra_leagues.json and re-checked later, which keeps the
number of requests down. If a league fails, its previous data is kept.

ESPN's feeds are free and keyless but unofficial, so they can change or disappear at any time.
Field events with no head-to-head matchup (golf and racing outrights) are not covered.

Usage: python fetch_extra.py [extra_lines.json]
"""
import json, os, re, sys, time, datetime as dt
import urllib.request, urllib.error

OUT = sys.argv[1] if len(sys.argv) > 1 else "extra_lines.json"
CACHE = "extra_athletes.json"   # athlete id -> [name, team abbr, position], so each player is looked up once
LEAGUE_CACHE = "extra_leagues.json"  # league -> name, sport, and when to check it again
DK = "100"  # ESPN's provider id for DraftKings
CORE = "https://sports.core.api.espn.com/v2/sports"
SITE = "https://site.api.espn.com/apis/site/v2/sports"
MAX_NEW_ATHLETES = int(os.environ.get("EXTRA_MAX_ATHLETES") or 1200)
BUDGET = int(os.environ.get("EXTRA_BUDGET_MIN") or 25) * 60  # stop starting new leagues after this long
MAX_PROP_CALLS = 60   # per league per run
KEYMAP = {"nfl": "NFL", "nba": "NBA", "college-football": "CFB"}  # same codes as lines.json
PROPS_ELSEWHERE = {"NFL", "NBA", "CFB"}
KNOWN_SPORTS = ["australian-football", "baseball", "basketball", "cricket", "field-hockey", "football", "golf",
                "hockey", "lacrosse", "mma", "racing", "rugby", "rugby-league", "soccer", "tennis", "volleyball", "water-polo"]
DAYS = {"nfl": 7, "ufc": 10, "college-football": 3}
DAYS_BY_SPORT = {"soccer": 4, "tennis": 2, "mma": 10, "rugby": 5, "cricket": 3}
DORMANT_HOURS, NO_ODDS_HOURS = 12, 6
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
        return None  # field events (golf, racing) have no head-to-head matchup here
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


def state_of(ev, comp):
    """A match's own status wins: a tennis tournament or UFC card can be 'in' while later matches are still 'pre'."""
    return pick(comp, "status", "type", "state") or pick(ev, "status", "type", "state") or "pre"


def prop_label(name):
    n = re.sub(r"\(.*?\)", "", name).strip()
    n = re.sub(r"^total\s+", "", n, flags=re.I).strip()
    return n[:1].upper() + n[1:] if n else n


def load_json(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def slug_of(ref, kind):
    m = re.search(rf"/{kind}/([^/?]+)", ref or "")
    return m.group(1) if m else None


def discover(sports_prev, log):
    """Ask ESPN which sports and leagues exist. Returns {'sport/slug': {'sport','slug'}}."""
    try:
        d = get(f"{CORE}?limit=100")
        sports = [s for s in (slug_of(i.get("$ref"), "sports") for i in d.get("items", [])) if s] or KNOWN_SPORTS
    except Exception as e:
        log(f"sports list: {e}")
        sports = KNOWN_SPORTS
    found = {}
    for sp in sports:
        slugs, page, pages = [], 1, 1
        try:
            while page <= pages:
                d = get(f"{CORE}/{sp}/leagues?limit=300&page={page}")
                slugs += [s for s in (slug_of(i.get("$ref"), "leagues") for i in d.get("items", [])) if s]
                pages = d.get("pageCount", 1) or 1
                page += 1
        except Exception as e:
            log(f"{sp} leagues: {e}")
            slugs = [k.split("/", 1)[1] for k in sports_prev if k.startswith(sp + "/")]  # fall back to what we knew
        for s in slugs:
            found[f"{sp}/{s}"] = {"sport": sp, "slug": s}
        time.sleep(0.1)
    return found


def main():
    started = time.time()
    now = dt.datetime.now(dt.timezone.utc)
    old = load_json(OUT, {})
    cache = load_json(CACHE, {})
    lcache = load_json(LEAGUE_CACHE, {})
    out = {"fetched": now.isoformat(timespec="minutes"), "book": "DraftKings", "leagues": {}, "log": []}
    log = out["log"].append
    new_lookups = 0

    def athlete(sport, slug, aid, teams):
        nonlocal new_lookups
        k = f"{slug}|{aid}"
        if k in cache:
            return cache[k]
        if new_lookups >= MAX_NEW_ATHLETES:
            return None
        new_lookups += 1
        try:
            a = get(f"{CORE}/{sport}/leagues/{slug}/athletes/{aid}?lang=en&region=us", tries=2)
        except Exception:
            return None
        tm = re.search(r"/teams/(\d+)", pick(a, "team", "$ref") or "")
        cache[k] = [a.get("displayName") or a.get("fullName") or "", teams.get(tm.group(1), "") if tm else "", pick(a, "position", "abbreviation") or ""]
        time.sleep(0.1)
        return cache[k]

    leagues = discover(lcache, log)
    print(f"{len(leagues)} leagues across {len({v['sport'] for v in leagues.values()})} sports")
    scanned = with_odds = 0
    for lk, info in sorted(leagues.items()):
        sport, slug = info["sport"], info["slug"]
        key = KEYMAP.get(slug, slug)
        prev = lcache.get(lk, {})
        if prev.get("skip_until") and now.isoformat() < prev["skip_until"]:
            continue
        if time.time() - started > BUDGET:
            log("time budget reached; remaining leagues checked next run")
            if key in (old.get("leagues") or {}):
                out["leagues"][key] = old["leagues"][key]
            continue
        extra_qs = "&groups=80&limit=400" if slug == "college-football" else ""
        base = f"{SITE}/{sport}/{slug}/scoreboard"
        events, seen, name = [], set(), prev.get("name") or slug
        try:
            first = get(f"{base}?{extra_qs.lstrip('&')}" if extra_qs else base)
            scanned += 1
            name = pick((first.get("leagues") or [{}])[0], "name") or name
            for e in first.get("events", []):
                if e["id"] not in seen:
                    seen.add(e["id"]); events.append(e)
            live = any(state_of(e, c) == "pre" for e in events for c in (competitions(e) or [{}]))
            if live:  # something is coming up, so look at the next few days too
                start = now.astimezone(dt.timezone(dt.timedelta(hours=-5))).date()
                for k in range(1, DAYS.get(slug, DAYS_BY_SPORT.get(sport, 3)) + 1):
                    day = get(f"{base}?dates={start + dt.timedelta(days=k):%Y%m%d}{extra_qs}")
                    for e in day.get("events", []):
                        if e["id"] not in seen:
                            seen.add(e["id"]); events.append(e)
                    time.sleep(0.1)
        except Exception as e:
            log(f"{lk} scoreboard: {e}")
            if key in (old.get("leagues") or {}):
                out["leagues"][key] = old["leagues"][key]
            continue
        time.sleep(0.1)
        games, teams, pre_comps = [], {}, []
        for ev in events:
            for comp in competitions(ev):
                if state_of(ev, comp) != "pre":
                    continue
                for c in comp.get("competitors") or []:
                    t = c.get("team") or {}
                    if t.get("id") and t.get("abbreviation"):
                        teams[str(t["id"])] = t["abbreviation"]
                g = comp_to_game(ev, comp)
                if g:
                    pre_comps.append((ev, comp, g))
                    if g["odds"]:
                        games.append(g)
        if not events:
            lcache[lk] = {"name": name, "sport": sport, "skip_until": (now + dt.timedelta(hours=DORMANT_HOURS)).isoformat(timespec="minutes")}
            continue
        if not games:
            lcache[lk] = {"name": name, "sport": sport, "skip_until": (now + dt.timedelta(hours=NO_ODDS_HOURS)).isoformat(timespec="minutes")}
            continue
        lcache[lk] = {"name": name, "sport": sport}
        props = []
        if key not in PROPS_ELSEWHERE:
            calls = misses = 0
            for ev, comp, g in sorted(pre_comps, key=lambda x: x[2]["t"] or ""):
                if not g["odds"] or calls >= MAX_PROP_CALLS or misses >= 8:
                    continue
                url = f"{CORE}/{sport}/leagues/{slug}/events/{ev['id']}/competitions/{g['id']}/odds/{DK}/propBets"
                items, page, pages = [], 1, 1
                calls += 1
                try:
                    while page <= pages:
                        d = get(f"{url}?limit=1000&page={page}")
                        items += d.get("items", [])
                        pages = d.get("pageCount", 1) or 1
                        page += 1
                except urllib.error.HTTPError as e:
                    misses += 1
                    if e.code != 404:
                        log(f"{lk} {g['away']}@{g['home']}: props {e.code}")
                    continue
                except Exception as e:
                    log(f"{lk} {g['away']}@{g['home']}: props {e}")
                    continue
                misses = 0
                best = {}
                for it in items:
                    tname = (it.get("type") or {}).get("name", "")
                    cur = pick(it, "current", "target", "value")
                    m = re.search(r"/athletes/(\d+)", pick(it, "athlete", "$ref") or "")
                    if not m or cur is None or not tname or SKIP.search(tname.lower()):
                        continue
                    k2 = (m.group(1), tname)
                    upd = it.get("lastUpdated", "")
                    if k2 not in best or upd > best[k2][0]:
                        best[k2] = (upd, cur, pick(it, "open", "target", "value"), tname)
                for (aid, _), (upd, cur, opn, tname) in best.items():
                    who = athlete(sport, slug, aid, teams)
                    if not who or not who[0]:
                        continue
                    props.append({"gid": g["id"], "t": g["t"], "away": g["away"], "home": g["home"],
                                  "aid": aid, "name": who[0], "team": who[1], "pos": who[2],
                                  "label": prop_label(tname), "line": cur, "open": opn, "u": upd})
                time.sleep(0.2)
            props = props[:6000]
        out["leagues"][key] = {"name": name, "sport": sport, "slug": slug, "games": games, "props": props}
        with_odds += 1
        print(f"{key}: {len(games)} games with DK odds, {len(props)} player props")
        log(f"{key}: {len(events)} events, {len(games)} with odds, {len(props)} props")
    if not scanned and not out["leagues"]:
        print("ESPN unreachable; keeping previous extra_lines.json")
        old["log"] = out["log"]
        out = old if old else out
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    json.dump(cache, open(CACHE, "w"), separators=(",", ":"))
    json.dump(lcache, open(LEAGUE_CACHE, "w"), separators=(",", ":"))
    print(f"wrote {OUT}: {with_odds} leagues with DraftKings odds ({scanned} scoreboards read, {new_lookups} new athlete lookups)")


if __name__ == "__main__":
    main()
