#!/usr/bin/env python3
"""
Cross-band views: what wins everywhere, and how a single opening performs
across all rating bands.

Input : db/openings.sqlite (opening_stats)
Output: site/data/cross/by_band.json   -- per band: openings ranked by score
        site/data/cross/<kind>.json    -- per opening: score in every band

Entry format: [name, band, n, w, d, l]  (w/d/l from White's POV)
"""
import argparse, json, os, sqlite3, time

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True)
ap.add_argument("--out", default="site")
ap.add_argument("--min-n", type=int, default=300)
ap.add_argument("--top", type=int, default=40)
a = ap.parse_args()

con = sqlite3.connect(a.db)
months = [r[0] for r in con.execute(
    "SELECT DISTINCT month FROM opening_stats ORDER BY month")]

os.makedirs(os.path.join(a.out, "data", "cross"), exist_ok=True)
written = 0
for month in months:
    for kind in ("pair", "eco"):
        # every (name, band) cell that clears the sample floor
        cells = {}
        for band, name, n, w, d, l in con.execute("""
                SELECT band, name, n, w, d, l FROM opening_stats
                WHERE month=? AND mode='avg' AND kind=? AND speed='blitz' AND n>=?""",
                (month, kind, a.min_n)):
            cells.setdefault(name, []).append([band, n, w, d, l])

        # index: how many bands each opening clears + its overall volume
        rank = []
        for name, rows in cells.items():
            tot = sum(r[1] for r in rows)
            ws = sum(r[2] + r[3] / 2 for r in rows) / tot
            mean_band_score = 0.0
            nz = sum(1 for r in rows if r[1])
            rank.append({"name": name, "bands": len(rows), "n": tot,
                         "score": ws, "rows": rows})
        rank.sort(key=lambda x: (-x["bands"], -x["n"]))
        keep = {r["name"] for r in rank[:a.top]}

        by_band = {}
        for r in rank:
            if r["name"] not in keep:
                continue
            for band, n, w, d, l in r["rows"]:
                by_band.setdefault(str(band), []).append([r["name"], n, w, d, l])

        out = {"meta": {"month": month, "kind": kind, "min_n": a.min_n,
                        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
               "bands": by_band,
               "cross": [[r["name"], r["bands"], r["n"], r["score"]]
                         for r in rank[:a.top]]}
        with open(os.path.join(a.out, "data", "cross", f"{kind}.json"),
                  "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
        written += 1
        print(f"{month} {kind}: {len(cells)} openings >= {a.min_n} games, "
              f"kept top {len(keep)}, bands={len(by_band)}")

print(f"written: {written}")
con.close()
