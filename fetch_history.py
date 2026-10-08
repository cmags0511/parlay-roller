"""Recent game logs for players who have a prop in MLB / NHL / WNBA etc. (ESPN athlete gamelog).
Writes extra_history.json: {"players": {"<league>|<athleteId>": {"t": fetched, "n": games, "v": {"<prop label>": [newest-first values]}}}, "log": [...]}.
Run after fetch_extra.py. Anything that can't be mapped just gets no history (the app falls back to a coin flip)."""
import json, sys, time, traceback, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
from fetch_extra import get

BASE = "https://site.web.api.espn.com/apis/common/v3/sports"
KEEP = 30          # games kept per stat
MIN_GAMES = 12     # fetch the previous season too when the current one has fewer games than this
FRESH_HOURS = 5    # reuse an entry fetched more recently than this
BUDGET = 20 * 60
MAX_PLAYERS = 1500


def n(row, *keys):
    for k in keys:
        v = row.get(k)
        if v is not None:
            return v
    return None


def add(*xs):
    return None if any(x is None for x in xs) else sum(xs)


def outs(ip):
    if ip is None:
        return None
    whole, _, frac = str(ip).partition(".")
    try:
        return int(whole) * 3 + int(frac or 0)
    except ValueError:
        return None


# label -> (row kind required or None, function(row) -> number|None)
STATS = {
    # baseball: batting rows have atBats, pitching rows have innings
    "Hits": ("bat", lambda r: n(r, "hits")),
    "Singles Hit": ("bat", lambda r: None if None in (n(r, "hits"), n(r, "doubles"), n(r, "triples"), n(r, "homeRuns")) else r["hits"] - r["doubles"] - r["triples"] - r["homeRuns"]),
    "Bases": ("bat", lambda r: None if None in (n(r, "hits"), n(r, "doubles"), n(r, "triples"), n(r, "homeRuns")) else r["hits"] + r["doubles"] + 2 * r["triples"] + 3 * r["homeRuns"]),
    "Hits + Runs + RBIs": ("bat", lambda r: add(n(r, "hits"), n(r, "runs"), n(r, "RBIs"))),
    "Runs Scored": ("bat", lambda r: n(r, "runs")),
    "RBIs": ("bat", lambda r: n(r, "RBIs")),
    "Stolen Bases": ("bat", lambda r: n(r, "stolenBases")),
    "Walks": ("bat", lambda r: n(r, "walks")),
    "Strikeouts": ("pit", lambda r: n(r, "strikeouts")),
    "Hits Allowed": ("pit", lambda r: n(r, "hits")),
    "Earned Runs Allowed": ("pit", lambda r: n(r, "earnedRuns")),
    "Walks Allowed": ("pit", lambda r: n(r, "walks")),
    "Outs Recorded": ("pit", lambda r: outs(r.get("innings"))),
    "Hits + Walks + Earned Runs Allowed": ("pit", lambda r: add(n(r, "hits"), n(r, "walks"), n(r, "earnedRuns"))),
    # hockey / basketball (same names work for NHL skaters, WNBA/NBA)
    "Points": (None, lambda r: n(r, "points")),
    "Assists": (None, lambda r: n(r, "assists")),
    "Rebounds": (None, lambda r: n(r, "totalRebounds", "rebounds")),
    "Shots on Goal": (None, lambda r: n(r, "shotsTotal", "shots")),
    "Saves": (None, lambda r: n(r, "saves")),
    "Blocked Shots": (None, lambda r: n(r, "blockedShots", "blocks")),
    "3-Point Field Goals": (None, lambda r: n(r, "threePointFieldGoalsMade", "threePointFieldGoals")),
    "Points and Assists": (None, lambda r: add(n(r, "points"), n(r, "assists"))),
    "Points and Rebounds": (None, lambda r: add(n(r, "points"), n(r, "totalRebounds", "rebounds"))),
    "Assists and Rebounds": (None, lambda r: add(n(r, "assists"), n(r, "totalRebounds", "rebounds"))),
    "Points, Rebounds, and Assists": (None, lambda r: add(n(r, "points"), n(r, "totalRebounds", "rebounds"), n(r, "assists"))),
}


def tonum(s):
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


def rows_of(js):
    """-> list of (date, {stat name: value}) from an ESPN gamelog payload, any shape variant."""
    names = js.get("names") or []
    ev = js.get("events") or {}
    out = []

    def one(eid, stats):
        if not isinstance(stats, list) or len(stats) < len(names):
            return
        row = {}
        for nm, raw in zip(names, stats):
            if "-" in nm and nm.count("-") == str(raw).count("-") and "-" in str(raw):
                for a, b in zip(nm.split("-"), str(raw).split("-")):
                    v = tonum(b)
                    if v is not None:
                        row[a] = v
                continue
            if nm == "innings":
                row[nm] = str(raw)
                continue
            v = tonum(raw)
            if v is not None:
                row[nm] = v
        date = ((ev.get(str(eid)) or {}).get("gameDate") or "")[:10]
        if row:
            out.append((date, row))

    for st in js.get("seasonTypes") or []:
        for cat in st.get("categories") or []:
            for e in cat.get("events") or []:
                one(e.get("eventId"), e.get("stats"))
    if not out:  # flat variant: events carry stats directly
        for eid, e in ev.items():
            if isinstance(e, dict) and e.get("stats"):
                one(eid, e["stats"])
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def season_of(js):
    for f in js.get("filters") or []:
        if f.get("name") == "season":
            try:
                return int(f.get("value"))
            except (TypeError, ValueError):
                return None
    return None


def kind_of(row):
    if "innings" in row:
        return "pit"
    if "atBats" in row:
        return "bat"
    return None


def fetch_player(lk, info, aid, labels):
    base = f"{BASE}/{info['sport']}/{info['slug']}/athletes/{aid}/gamelog"
    js = get(base)
    rows = rows_of(js)
    need = {STATS[l][0] for l in labels if l in STATS} - {None}
    have = {kind_of(r) for _, r in rows[:1]} - {None}
    if need and have and not need & have:  # e.g. a pitcher whose prop is a batting stat
        try:
            js2 = get(base + "?category=" + ("batting" if "bat" in need else "pitching"))
            rows = rows_of(js2)
            js = js2
        except Exception:
            pass
    if len(rows) < MIN_GAMES:
        s = season_of(js)
        if s:
            try:
                q = base + "?" + urllib.parse.urlencode({"season": s - 1})
                rows += rows_of(get(q))
                rows.sort(key=lambda x: x[0], reverse=True)
            except Exception:
                pass
    vals = {}
    for lab in labels:
        spec = STATS.get(lab)
        if not spec:
            continue
        kind, fn = spec
        seq = []
        for _, r in rows:
            if kind and kind_of(r) != kind:
                continue
            try:
                v = fn(r)
            except (TypeError, KeyError):
                v = None
            if v is not None:
                seq.append(v if v != int(v) else int(v))
        if seq:
            vals[lab] = seq[:KEEP]
    return {"n": len(rows), "v": vals}


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "extra_history.json"
    extra = json.load(open("extra_lines.json"))
    try:
        old = json.load(open(out_path))
    except Exception:
        old = {}
    oldp = old.get("players") or {}
    now = dt.datetime.now(dt.timezone.utc)
    log = []
    want = {}
    for lk, L in (extra.get("leagues") or {}).items():
        for r in L.get("props") or []:
            if r.get("label") in STATS and r.get("aid"):
                want.setdefault((lk, str(r["aid"])), (L, set()))[1].add(r["label"])
    items = list(want.items())[:MAX_PLAYERS]
    t0 = time.time()
    players = {}

    def work(it):
        (lk, aid), (L, labels) = it
        key = f"{lk}|{aid}"
        prev = oldp.get(key)
        if prev:
            age = (now - dt.datetime.fromisoformat(prev["t"])).total_seconds() / 3600
            if age < FRESH_HOURS and set(labels) <= set(prev.get("v") or {}):
                return key, prev
        if time.time() - t0 > BUDGET:
            return key, prev
        try:
            res = fetch_player(lk, L, aid, labels)
            res["t"] = now.isoformat(timespec="minutes")
            return key, res
        except Exception as ex:
            tb = traceback.extract_tb(ex.__traceback__)[-1]
            log.append(f"{key}: {type(ex).__name__}: {ex} (line {tb.lineno})")
            return key, prev

    with ThreadPoolExecutor(8) as ex:
        for key, res in ex.map(work, items):
            if res:
                players[key] = res
    got = sum(1 for p in players.values() if p.get("v"))
    log.insert(0, f"{len(items)} players wanted, {got} with history, {len(log)} errors")
    json.dump({"fetched": now.isoformat(timespec="minutes"), "players": players, "log": log[:80]}, open(out_path, "w"), separators=(",", ":"))
    print(log[0])
    for l in log[1:6]:
        print(l)


if __name__ == "__main__":
    main()
