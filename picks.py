"""Rank the next slate's player props against DraftKings lines and write picks.json.

Reads props_data.json (game logs + schedule) and lines.json (DraftKings lines from ESPN).
Writes the top 20 candidates; the app shows the first 10 that the daily news check
(notes.json) hasn't ruled out. No third-party packages needed.

Usage: python picks.py [picks.json]
"""
import json, math, re, sys, datetime as dt

OUT = sys.argv[1] if len(sys.argv) > 1 else "picks.json"
KEEP = 20

UNIT = {"pyd": "passing yards", "cmp": "completions", "att": "pass attempts", "ptd": "passing TDs",
        "ryd": "rushing yards", "car": "carries", "rec": "receptions", "recyd": "receiving yards",
        "rry": "rush + rec yards", "pts": "points", "reb": "rebounds", "ast": "assists", "3pm": "threes",
        "pra": "pts + reb + ast", "pr": "pts + reb", "pa": "pts + ast", "ra": "reb + ast",
        "fgm": "field goals", "pat": "extra points", "kpts": "kicking points", "tkl": "tackles + assists",
        "sck": "sacks"}
LABEL = {"pyd": "Passing yards", "cmp": "Completions", "att": "Pass attempts", "ptd": "Passing TDs",
         "ryd": "Rushing yards", "car": "Rush attempts", "rec": "Receptions", "recyd": "Receiving yards",
         "rry": "Rush + Rec yards", "pts": "Points", "reb": "Rebounds", "ast": "Assists",
         "3pm": "3-pointers made", "pra": "Pts + Reb + Ast", "pr": "Pts + Reb", "pa": "Pts + Ast",
         "ra": "Reb + Ast", "fgm": "Field goals made", "pat": "Extra points made", "kpts": "Kicking points",
         "tkl": "Tackles + assists", "sck": "Sacks"}


def stat(g, m):
    if m == "rry": return g["ryd"] + g["recyd"]
    if m == "kpts": return 3 * g["fgm"] + g["pat"]
    if m == "pra": return g["pts"] + g["reb"] + g["ast"]
    if m == "pr": return g["pts"] + g["reb"]
    if m == "pa": return g["pts"] + g["ast"]
    if m == "ra": return g["reb"] + g["ast"]
    return g[m]


def fmt(v):
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}"


def main():
    data = json.load(open("props_data.json"))
    try:
        data["CFB"] = json.load(open("cfb_data.json"))
    except Exception:
        pass
    try:
        lines = json.load(open("lines.json"))
    except Exception:
        lines = {}
    now = dt.datetime.now(dt.timezone.utc)
    cands = []
    for lg in ("NFL", "NBA", "CFB"):
        if lg not in data:
            continue
        L = lines.get(lg) or {}
        meta = data[lg]
        cols = meta["cols"]
        ups = [{"t": dt.datetime.fromisoformat(u[0]), "away": u[1], "home": u[2], "spread": u[3], "total": u[4]}
               for u in meta.get("upcoming", [])]
        ups = [u for u in ups if u["t"] >= now - dt.timedelta(hours=3)]
        for p in meta["players"]:
            espn = p.get("e") or p["id"]
            e = L.get(espn)
            if not e:
                continue
            game = next((u for u in sorted(ups, key=lambda u: u["t"]) if p["t"] in (u["away"], u["home"])), None)
            if not game:
                continue
            games = [dict(zip(cols, g)) for g in p["g"]]
            for mid, dk in e.items():
                if mid not in UNIT:
                    continue
                if dk.get("d") and dt.datetime.fromisoformat(dk["d"].replace("Z", "+00:00")) < now - dt.timedelta(hours=5):
                    continue  # line for a game that's already been played
                vals = [stat(g, mid) for g in games]
                if len(vals) < (5 if lg == "CFB" else 8):
                    continue
                line = dk["line"]
                cur = [stat(g, mid) for g in games if g["date"] >= meta["curStart"]]
                l10, l5 = vals[-10:], vals[-5:]
                hits = lambda a: sum(v > line for v in a)
                sh = lambda a: (hits(a) + 1) / (len(a) + 2)
                p_over = .45 * sh(l10) + .35 * (sh(cur) if len(cur) >= 3 else sh(vals)) + .2 * sh(l5)
                avg = sum(l10) / len(l10)
                sd = max(math.sqrt(sum((v - avg) ** 2 for v in l10) / len(l10)), line * .12, 1)
                z = (avg - line) / sd
                over = p_over >= .5
                score = (p_over - .5 if over else .5 - p_over) * 2 + .25 * (z if over else -z)
                if score <= 0:
                    continue
                home = game["home"] == p["t"]
                opp = game["away"] if home else game["home"]
                side_hits = lambda a: hits(a) if over else len(a) - hits(a)
                last = re.sub(r"\s+(Jr|Sr|II|III|IV|V)\.?$", "", p["n"]).split()[-1]
                why = (f"{last} has gone {'over' if over else 'under'} {fmt(line)} in {side_hits(l10)} of his last "
                       f"{len(l10)}")
                if len(cur) >= 2:
                    why += f" and {side_hits(cur)} of {len(cur)} this season"
                why += f", averaging {avg:.1f} {UNIT[mid]} over the last 10."
                vs = [stat(g, mid) for g in games if g["opp"] == opp]
                if vs:
                    why += f" Against {opp}: {', '.join(str(v) for v in vs)}."
                if dk.get("open") is not None and dk["open"] != line:
                    why += (f" The line has moved {'up' if line > dk['open'] else 'down'} "
                            f"{fmt(abs(line - dk['open']))} since it opened at {fmt(dk['open'])}.")
                if lg in ("NFL", "CFB") and game["total"] is not None:
                    sp = game["spread"] if home else (None if game["spread"] is None else -game["spread"])
                    why += f" Game total {fmt(game['total'])}"
                    why += f", {p['t']} {'+' if sp > 0 else ''}{fmt(sp)}." if sp is not None else "."
                cands.append({
                    "key": f"{lg}|{p['id']}|{mid}", "lg": lg, "id": p["id"], "name": p["n"], "team": p["t"],
                    "pos": p.get("p"), "opp": opp, "home": home, "t": game["t"].isoformat(), "market": mid,
                    "label": LABEL[mid], "side": "over" if over else "under", "line": line, "open": dk.get("open"),
                    "l10": [side_hits(l10), len(l10)], "avg10": round(avg, 1), "score": round(score, 3), "why": why,
                })
    out = {"built": now.isoformat(timespec="minutes"), "picks": []}
    if cands:
        first = min(dt.datetime.fromisoformat(c["t"]) for c in cands)

        def pick_from(pool):
            pool = sorted(pool, key=lambda c: -c["score"])
            res, seen, per_game = [], set(), {}
            for c in pool:
                gk = (c["lg"], c["t"], *sorted((c["team"], c["opp"])))
                if (c["lg"], c["id"]) in seen or per_game.get(gk, 0) >= 3:
                    continue
                seen.add((c["lg"], c["id"])); per_game[gk] = per_game.get(gk, 0) + 1
                res.append(c)
                if len(res) == KEEP:
                    break
            return res

        def slate(pool, need):
            # start with the next day's games and widen until there are enough picks
            start = min(dt.datetime.fromisoformat(c["t"]) for c in pool)
            chosen = []
            for hours in (18, 42, 96, 168):
                chosen = pick_from([c for c in pool if dt.datetime.fromisoformat(c["t"]) <= start + dt.timedelta(hours=hours)])
                if len(chosen) >= need:
                    break
            return chosen

        out["picks"] = slate([c for c in cands if c["score"] > .12], 14)  # 10 shown + spares for ruled-out players
        out["byMarket"] = {}
        for k in sorted({(c["lg"], c["market"]) for c in cands}):
            pool = [c for c in cands if (c["lg"], c["market"]) == k]
            out["byMarket"][f"{k[0]}|{k[1]}"] = slate(pool, 12)[:12]
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    print(f"wrote {OUT}: {len(out['picks'])} candidates")


if __name__ == "__main__":
    main()
