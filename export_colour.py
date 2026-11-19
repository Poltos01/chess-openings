#!/usr/bin/env python3
"""
Per-band colour balance: how White and Black actually score in each rating band.

Input : db/openings.sqlite written by ingest.py (opening_stats)
Output: site/data/colour/<month>/<mode>.json

Each entry: [band, n, w, d, l] with w/d/l from WHITE's point of view, so
White's score = (w + d/2)/n and Black's = (l + d/2)/n.
"""
import argparse, json, os, sqlite3, time

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True)
ap.add_argument("--out", default="site")
a = ap.parse_args()

con = sqlite3.connect(a.db)
months = [r[0] for r in con.execute(
    "SELECT DISTINCT month FROM opening_stats ORDER BY month")]

written = 0
for month in months:
    bundle = {"meta": {"month": month, "generated_at":
                       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
              "avg": [], "strict": []}
    for mode in ("avg", "strict"):
        # pair rows are disjoint per (speed, band), so summing them per band is
        # exact: the same game is counted once per band, per speed.
        for band, n, w, d, l in con.execute("""
                SELECT band, SUM(n), SUM(w), SUM(d), SUM(l)
                FROM opening_stats
                WHERE month=? AND mode=? AND kind='pair'
                GROUP BY band ORDER BY band""", (month, mode)):
            bundle[mode].append([band, n, w, d, l])
    d = os.path.join(a.out, "data", "colour", month)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "bands.json"), "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, ensure_ascii=False, separators=(",", ":"))
    written += 1
    for mode in ("avg", "strict"):
        rows = bundle[mode]
        tot = sum(r[1] for r in rows) or 1
        ws = sum(r[2] + r[3] / 2 for r in rows) / tot
        print(f"{month} {mode}: {len(rows)} bands, "
              f"{tot:,} games, White score {ws*100:.2f}%")

print(f"written: {written} files")
con.close()
