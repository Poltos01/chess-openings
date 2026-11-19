#!/usr/bin/env python3
"""
Lichess opening-statistics ingest (fast).

Streams a monthly PGN.zst dump (HTTP range or local file), decodes on the fly,
aggregates into SQLite. Never stores the PGN; ECO/Opening come from the PGN headers.

Rating hygiene: games with |WhiteElo-BlackElo| > --gap-max are dropped unless
max(elo) >= --elite (default 80 / 2400).
"""
import argparse, re, sqlite3, time, urllib.error, urllib.request
import zstandard as zstd

# only the fields we actually use -> 7 decodes per game instead of 40
NEEDED = re.compile(
    rb'\[(WhiteElo|BlackElo|Result|Variant|ECO|Opening|TimeControl) "([^"]*)"\]')
COMMENT = re.compile(rb'\{[^}]*\}')
FIRST_PAIR = re.compile(rb'1\.\s*(\S+)\s+1\.\.\.\s+(\S+)')
RESULT = {b"1-0": 1, b"0-1": 3, b"1/2-1/2": 2}
SEP = b"\n\n[Event "
CHUNK = 1 << 22

SCHEMA = """
CREATE TABLE IF NOT EXISTS opening_stats (
  month TEXT NOT NULL, speed TEXT NOT NULL, band INTEGER NOT NULL,
  mode TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
  n INTEGER NOT NULL, w INTEGER NOT NULL, d INTEGER NOT NULL, l INTEGER NOT NULL,
  PRIMARY KEY (month, speed, band, mode, kind, name)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_lookup ON opening_stats(month, band, mode, speed, kind);
"""


def classify_speed(tc):
    if not tc or tc == "-":
        return "correspondence"
    try:
        secs = int(tc.split("+")[0])
    except ValueError:
        return "unknown"
    return ("ultraBullet" if secs < 30 else "bullet" if secs < 180 else
            "blitz" if secs < 480 else "rapid" if secs < 1500 else "classical")


class ChunkReader:
    """Minimal file-like wrapper; hands out chunks without re-copying them."""

    def __init__(self, gen):
        self.gen, self.buf, self.done = gen, b"", False

    def read(self, n=CHUNK):
        while len(self.buf) < n:
            if self.done:
                break
            try:
                self.buf += next(self.gen)
            except StopIteration:
                self.done = True
        out, self.buf = self.buf[:n], self.buf[n:]
        return out


def byte_stream(source, offset, max_bytes):
    if source.startswith("http"):
        req = urllib.request.Request(source, headers={"User-Agent": "Mozilla/5.0"})
        if offset or max_bytes:
            end = "" if not max_bytes else str(offset + max_bytes - 1)
            req.add_header("Range", f"bytes={offset}-{end}")
        fh = urllib.request.urlopen(req, timeout=180)
    else:
        fh = open(source, "rb")
        fh.seek(offset)
    got = 0
    try:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                return
            got += len(chunk)
            if max_bytes and got >= max_bytes:
                yield chunk[: len(chunk) - (got - max_bytes)]
                return
            yield chunk
    finally:
        fh.close()


def games(stream):
    """Yield game bytes. One cursor over one buffer: no per-game re-copy of the tail."""
    reader = zstd.ZstdDecompressor().stream_reader(ChunkReader(stream))
    buf, pos = b"", 0
    while True:
        try:
            chunk = reader.read(CHUNK)
        except Exception:
            break          # truncated tail: everything before it is valid
        if not chunk:
            break
        if pos:
            buf, pos = buf[pos:], 0
        buf += chunk
        while True:
            i = buf.find(SEP, pos)
            if i < 0:
                break
            yield buf[pos:i]
            pos = i + 2
    # trailing partial game dropped on purpose


MAGIC = b"\x28\xb5\x2f\xfd"


def size_of(source):
    req = urllib.request.Request(source, method="HEAD",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return int(r.headers["Content-Length"])


def _open(source, a, b):
    req = urllib.request.Request(source, headers={
        "User-Agent": "Mozilla/5.0", "Range": f"bytes={a}-{b}"})
    return urllib.request.urlopen(req, timeout=180)


def robust_stream(source, start, end):
    """Sequential HTTP stream that survives drops, 429s and short reads.

    On any failure it reconnects from the last byte position and re-aligns to
    the next zstd frame magic (losing at most one frame per reconnect).
    """
    pos = start
    scan = start > 0
    stalls = 0
    while pos < end:
        try:
            fh = _open(source, pos, end)
        except urllib.error.HTTPError as e:
            if e.code == 429 and stalls < 20:
                stalls += 1
                time.sleep(3 + stalls * 4)
                continue
            raise
        except Exception:
            stalls += 1
            if stalls > 20:
                raise
            time.sleep(3 + stalls * 4)
            continue
        base, produced, carry = pos, 0, b""
        aligned = not scan
        try:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                window = carry + chunk
                win_base = base + produced - len(carry)
                produced += len(chunk)
                if not aligned:
                    i = window.find(MAGIC, max(0, pos - win_base))
                    if i < 0:
                        carry = window[-3:]
                        pos = base + produced
                        continue
                    aligned = True
                    out = window[i:]
                else:
                    out = chunk
                pos = base + produced
                stalls = 0
                yield out
                if pos >= end:
                    return
                carry = window[-3:]
        except Exception:
            pass
        finally:
            try:
                fh.close()
            except Exception:
                pass
        if pos <= base:
            stalls += 1
            if stalls > 20:
                raise RuntimeError(f"no progress at byte {base}")
            time.sleep(3 + stalls * 4)
            scan = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--month", required=True)
    ap.add_argument("--db", default="openings.sqlite")
    ap.add_argument("--max-bytes", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--gap-max", type=int, default=80)
    ap.add_argument("--elite", type=int, default=2400)
    a = ap.parse_args()

    con = sqlite3.connect(a.db)
    con.executescript(SCHEMA)
    agg = {}
    seen = used = gap_dropped = 0
    t0 = time.time()

    stream = (robust_stream(a.source, a.offset, size_of(a.source))
              if a.source.startswith("http") else byte_stream(a.source, a.offset, a.max_bytes))
    for g in games(stream):
        seen += 1
        cut = g.find(b"\n\n", 10)
        if cut < 0:
            continue
        h = {k.decode(): v.decode() for k, v in NEEDED.findall(g[:cut])}
        if h.get("Variant", "Standard") != "Standard":
            continue
        idx_w = RESULT.get(h.get("Result", "").encode())
        if not idx_w:
            continue
        try:
            we = int(h["WhiteElo"])
            be = int(h["BlackElo"])
        except (KeyError, ValueError):
            continue
        if abs(we - be) > a.gap_max and max(we, be) < a.elite:
            gap_dropped += 1
            continue
        m = FIRST_PAIR.search(COMMENT.sub(b" ", g[cut:cut + 400]))
        if not m:
            continue

        speed = classify_speed(h.get("TimeControl", ""))
        pair = "1." + re.sub(r"[?!]+", "", m.group(1).decode()) + " " + \
               re.sub(r"[?!]+", "", m.group(2).decode())
        eco = h.get("ECO", "?") + " " + h.get("Opening", "?")

        avg_band = (we + be) // 200 * 100                 # mean elo, 100-wide
        lo, hi = (we, be) if we <= be else (be, we)
        strict_band = lo // 100 * 100 if lo // 100 == hi // 100 else None

        for band, mode in ((avg_band, "avg"), (strict_band, "strict")):
            if band is None:
                continue
            for kind, name in (("pair", pair), ("eco", eco)):
                k = (speed, band, mode, kind, name)
                row = agg.get(k)
                if row is None:
                    row = agg[k] = [0, 0, 0, 0]
                row[0] += 1
                row[idx_w] += 1
        used += 1

    rows = [(a.month, sp, band, mode, kind, name, *v)
            for (sp, band, mode, kind, name), v in agg.items()]
    con.executemany("INSERT OR REPLACE INTO opening_stats VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()

    el = time.time() - t0
    print(f"seen={seen:,} used={used:,} gap_dropped={gap_dropped:,} rows={len(rows):,} in {el:.1f}s")
    print(f"rate={seen/el:,.0f} games/s  sanity="
          f"{'OK' if all(v[1]+v[2]+v[3] == v[0] for v in agg.values()) else 'BAD'}")

    cur = con.execute("""SELECT band, name, n, w, d, l FROM opening_stats
                         WHERE month=? AND mode='avg' AND kind='pair' AND speed='blitz'
                           AND band IN (1100,1200) AND n>=200
                         ORDER BY band, (w+0.5*d)*1.0/n DESC LIMIT 6""", (a.month,))
    print("\nspot-check blitz/avg:")
    for band, name, n, w, d, l in cur:
        print(f"  {band}-{band+99:<5} {name:<9} N={n:<5} score={(w+0.5*d)/n*100:5.1f}%")
    con.close()


if __name__ == "__main__":
    main()
