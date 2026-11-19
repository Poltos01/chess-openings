#!/usr/bin/env python3
"""
Export opening_stats.sqlite into per-slice JSON for the static site.

Output layout:
  data/index.json                      -- months / speeds / bands / totals
  data/<month>/<speed>/<band>.json     -- {meta, avg:{pair,eco}, strict:{pair,eco}}
Each list entry: [name, n, w, d, l]  (w/d/l are White's results)
"""
import argparse, json, os, sqlite3, time

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True)
ap.add_argument("--out", default="site")
ap.add_argument("--min-n", type=int, default=20)
ap.add_argument("--month-label", default=None, help="human label, e.g. 'октябрь 2026'")
a = ap.parse_args()

con = sqlite3.connect(a.db)
con.row_factory = sqlite3.Row

months = [r[0] for r in con.execute("SELECT DISTINCT month FROM opening_stats ORDER BY month")]
speeds = [r[0] for r in con.execute(
    "SELECT DISTINCT speed FROM opening_stats ORDER BY speed")]
bands = [r[0] for r in con.execute(
    "SELECT DISTINCT band FROM opening_stats ORDER BY band")]
index = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "months": months, "speeds": speeds, "bands": bands, "slices": {}}

os.makedirs(a.out, exist_ok=True)
written = 0
for month in months:
    for speed in speeds:
        for band in bands:
            row = con.execute(
                "SELECT mode, kind, name, n, w, d, l FROM opening_stats "
                "WHERE month=? AND speed=? AND band=? AND n>=?",
                (month, speed, band, a.min_n)).fetchall()
            if not row:
                continue
            bundle = {"meta": {"month": month, "speed": speed, "band": band,
                               "label": a.month_label or month},
                      "avg": {"pair": [], "eco": []},
                      "strict": {"pair": [], "eco": []}}
            totals = {"avg": [0, 0, 0, 0], "strict": [0, 0, 0, 0]}
            for r in row:
                bundle[r["mode"]][r["kind"]].append(
                    [r["name"], r["n"], r["w"], r["d"], r["l"]])
            for mode in ("avg", "strict"):
                for kind in ("pair", "eco"):
                    bundle[mode][kind].sort(key=lambda x: -x[1])
                    seen = set()
                    uniq = []
                    for e in bundle[mode][kind]:
                        if e[0] in seen:
                            continue
                        seen.add(e[0])
                        uniq.append(e)
                    bundle[mode][kind] = uniq
            for r in row:                     # totals per mode (pairs are disjoint)
                if r["kind"] == "pair":
                    t = totals[r["mode"]]
                    t[0] += r["n"]
                    t[1] += r["w"]
                    t[2] += r["d"]
                    t[3] += r["l"]
            for mode in ("avg", "strict"):
                bundle["meta"]["totals_" + mode] = totals[mode]

            d = os.path.join(a.out, "data", month, speed)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f"{band}.json"), "w", encoding="utf-8") as fh:
                json.dump(bundle, fh, ensure_ascii=False, separators=(",", ":"))
            index["slices"][f"{month}/{speed}/{band}"] = totals["avg"][0]
            written += 1

# Rebuild the index from whatever months are on disk: each ingest run adds one
# month's slices, so the DB never has to be committed.
root = os.path.join(a.out, "data")
months_fs, speeds_fs, bands_fs = [], set(), set()
if os.path.isdir(root):
    months_fs = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    for m in months_fs:
        for sp in os.listdir(os.path.join(root, m)):
            speeds_fs.add(sp)
            for f in os.listdir(os.path.join(root, m, sp)):
                if f.endswith(".json"):
                    bands_fs.add(int(f[:-5]))
ORDER = ["ultraBullet", "bullet", "blitz", "rapid", "classical", "correspondence"]
index = {
    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "months": months_fs,
    "speeds": [s for s in ORDER if s in speeds_fs] + sorted(speeds_fs - set(ORDER)),
    "bands": sorted(bands_fs),
}

with open(os.path.join(a.out, "data", "index.json"), "w", encoding="utf-8") as fh:
    json.dump(index, fh, ensure_ascii=False, separators=(",", ":"))

# ship the front-end next to the data so `out/` is a ready-to-deploy static root
import shutil
src_html = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
if os.path.exists(src_html):
    shutil.copy(src_html, os.path.join(a.out, "index.html"))
    print("copied index.html ->", os.path.join(a.out, "index.html"))

print(f"slices written: {written}, months in db={months}, bands={len(bands)}")
print(f"index rebuilt from disk: months={months_fs}, bands={len(bands_fs)}")
