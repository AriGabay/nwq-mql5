"""Dukascopy XAUUSD data check (user approval 2026-10-05): python research/duka_cli.py <command> [month]

Data only: no strategy run, no P&L, no optimization, no parameter choice. Fixed scope: January 2024 (an early
sample) and March 2026 (compared with Bybit XAUUSD.s; already exposed). No other months.

- Raw hour files are kept as downloaded under RAW (outside git, never overwritten).
- Committed outputs are code, manifests and summaries under results/dukascopy_v1, never ticks or bars.

Commands:
- download <month>: fetch every UTC hour of a month from the free Dukascopy feed, one request at a time. A 429
  answer (rate limit) stops the run; it resumes later where it stopped.
- manifest <month>: source, period, file count, tick count and sha256 per file.
- quality <month>: prices, spread, time order, duplicates, gaps, density and M1/M5 bars (bars stay in RAW).
- export <month>: the ticks as a binary file in the isolated copy's MQL5/Files for the import script.
- import: one offline start of the isolated copy that runs duka_import.mq5 (CLAUDE.md limited exception).
- verify-import <month>: the terminal's readback against the source, tick by tick.
- tester-dump <symbol> <month>: duka_tick_dump.mq5 in the isolated Tester (real ticks; it never trades).
- verify-tester <month>: the ticks the Tester delivered for XAUUSD.duka against the source.
- manual-check: the manual web export of one hour (two CSV files, ASK and BID, in MANUAL) against the feed file
  of the same hour, row by row; no ticks are written from the CSV.
- export-sample: the one-hour sample (2024-01-02 10:00 UTC, the feed file the manual export matched) for the
  import path check; `import --sample`, `verify-import sample`, `tester-dump XAUUSD.duka sample` and
  `verify-tester sample` then check the import path on that hour only (never a month's coverage).
- compare <month>: March 2026 Dukascopy against the Tester ticks of Bybit XAUUSD.s (clock, sessions, prices,
  spread, density). Nothing is shifted or adjusted in the stored data.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from mt5r import compile as compmod, dukascopy as dk, env, evaluate, ini, runner  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
RAW = pathlib.Path(os.environ.get("NWQ_DUKA_DIR", "D:/data/dukascopy"))
RESULTS = REPO / "results" / "dukascopy_v1"
SYMBOL = "XAUUSD"
CUSTOM = "XAUUSD.duka"
BYBIT = "XAUUSD.s"
MONTHS = {"2024-01": (2024, 1), "2026-03": (2026, 3)}       # the approved scope; nothing else
HOLIDAYS = [dt.date(2024, 1, 1)]
SAMPLE = "sample"                                            # one hour inside January 2024, for the import path
SAMPLE_HOUR = dt.datetime(2024, 1, 2, 10, tzinfo=dt.timezone.utc)
MANUAL = pathlib.Path(os.environ.get("NWQ_DUKA_MANUAL", str(REPO / "data")))   # gitignored
MANUAL_FILES = {s: f"XAU-USD_1Tick_{s}_2024-01-02_10_00-10_00_Etc_UTC.csv" for s in ("ASK", "BID")}
UA = "nwq-mql5 data check (python urllib; one request at a time)"
SRC_DIR = REPO / "research" / "mql5"
IMPORT_SCRIPT = "duka_import.mq5"
DUMP_EA = "duka_tick_dump.mq5"


class RateLimited(RuntimeError):
    """The feed answered 429; ``retry_after`` holds the server's Retry-After in seconds when it sent one."""

    def __init__(self, url, retry_after=None):
        super().__init__(url)
        self.url, self.retry_after = url, retry_after


class NetworkError(RuntimeError):
    """No answer after a few tries: the hour stays unresolved (never treated as a closed hour)."""


def check_month(key: str) -> tuple:
    if key not in MONTHS:
        raise SystemExit(f"{key}: only {sorted(MONTHS)} are approved for this data check")
    return MONTHS[key]


def raw_path(hour: dt.datetime) -> pathlib.Path:
    return RAW / SYMBOL / f"{hour.year:04d}" / f"{hour.month - 1:02d}" / f"{hour.day:02d}" / f"{hour.hour:02d}h_ticks.bi5"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iso(ms) -> str:
    return dt.datetime.fromtimestamp(int(ms) / 1000, dk.UTC).isoformat(timespec="milliseconds")


# ------------------------------------------------------------------ download and manifest
class BudgetSpent(RuntimeError):
    """The shared download budget is spent: no further HTTP request is sent."""


class Budget:
    """The shared budget for both months (requests sent and seconds waited), kept between runs. Every HTTP
    request, a retry included, is checked and counted before it is sent; every wait is counted before it starts.
    Counts kept before this per-request counting (2026-10-05) were per download attempt, so they may be lower
    than the requests actually sent; they are kept, not reset."""

    def __init__(self):
        self.b = load_budget()
        if "counting" not in self.b:                       # kept, never reset: the earlier count is recorded as-is
            self.b["counting"] = {"per_http_request_since": now_iso(), "count_before": self.b["requests"],
                                  "note": "counts before this time were per download attempt, not per HTTP "
                                          "request; they may be lower than the requests actually sent"}
            _write_json(budget_path(), self.b)

    def left(self) -> str:
        return budget_left(self.b)

    def request(self) -> None:
        why = self.left()
        if why:
            raise BudgetSpent(why)
        self.b["requests"] += 1
        _write_json(budget_path(), self.b)

    def wait(self, seconds: float) -> None:
        if self.b["wait_seconds"] + seconds > self.b["max_wait_seconds"]:
            raise BudgetSpent(f"wait budget would be exceeded ({self.b['wait_seconds']} + {seconds} > "
                              f"{self.b['max_wait_seconds']} s)")
        self.b["wait_seconds"] += seconds
        _write_json(budget_path(), self.b)
        time.sleep(seconds)


def fetch(u: str, budget: "Budget", tries: int = 4) -> tuple:
    """(http status, body). 404 is an answer (no file for that hour); 429 raises RateLimited at once; network
    errors are retried a few times, then raised. Every HTTP request and every retry pause goes through the
    budget first."""
    err = None
    for k in range(tries):
        budget.request()
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 404, b""
            if e.code == 429:
                ra = e.headers.get("Retry-After") if e.headers else None
                raise RateLimited(u, int(ra) if ra and ra.strip().isdigit() else None) from e
            err = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            err = e
        if k + 1 < tries:
            why = budget.left()                             # no pause before a request the budget would refuse
            if why:
                raise BudgetSpent(why)
            budget.wait(5 * (k + 1))
    raise NetworkError(f"{u}: {err}")


def status_path(month: str) -> pathlib.Path:
    return RAW / SYMBOL / f"download_{month}.json"


def load_status(month: str) -> dict:
    p = status_path(month)
    st = json.loads(p.read_text()) if p.exists() else {}
    for k, v in (("hours", {}), ("rate_limits", []), ("runs", [])):
        st.setdefault(k, v)
    return st


def _write_json(p: pathlib.Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    tmp.replace(p)


def save_status(month: str, st: dict) -> None:
    _write_json(status_path(month), st)


def now_iso() -> str:
    return dt.datetime.now(dk.UTC).isoformat(timespec="seconds")


# one budget for both months, kept between runs: requests sent and seconds waited after 429s or dropped connections
BUDGET_DEFAULT = {"max_requests": 2500, "max_wait_seconds": 12 * 3600}


def budget_path() -> pathlib.Path:
    return RAW / SYMBOL / "download_budget.json"


def load_budget() -> dict:
    p = budget_path()
    b = json.loads(p.read_text()) if p.exists() else {**BUDGET_DEFAULT, "requests": 0, "wait_seconds": 0}
    return b


def budget_left(b: dict) -> str:
    """Empty while the budget lasts; otherwise why it is spent."""
    if b["requests"] >= b["max_requests"]:
        return f"request budget spent ({b['requests']}/{b['max_requests']})"
    if b["wait_seconds"] >= b["max_wait_seconds"]:
        return f"wait budget spent ({b['wait_seconds']}/{b['max_wait_seconds']} s)"
    return ""


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class DownloadLock:
    """At most one download at a time (no parallel requests to the feed)."""

    def __init__(self):
        self.path = RAW / SYMBOL / "download.lock"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            pid = int(json.loads(self.path.read_text()).get("pid", 0))
            if pid and _pid_alive(pid):
                raise SystemExit(f"another download is running (pid {pid}); refusing a parallel one")
            self.path.unlink()                              # a stale lock from a stopped run
        with open(self.path, "x") as f:
            json.dump({"pid": os.getpid(), "at": now_iso()}, f)
        return self

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)
        return False


def cmd_download(args) -> None:
    """Every hour of the month once: a stored file (possibly an empty 200 answer), a 404, or - after network
    failures - unresolved. A 429 waits Retry-After (or args.wait seconds) and retries the same hour; dropped
    connections wait the same way. Every wait is logged, and the shared budget (requests and seconds waited,
    kept between runs) stops the run with a partial summary when it is spent."""
    y, m = check_month(args.month)
    with DownloadLock():
        st, budget = load_status(args.month), Budget()
        b = budget.b
        t_end = time.time() + args.max_hours * 3600
        got, stop = 0, None
        for h in dk.month_hours(y, m):
            key = h.isoformat()
            p = raw_path(h)
            if p.exists():                                  # never overwritten
                st["hours"].setdefault(key, {"status": "file" if p.stat().st_size else "empty_response"})
                continue
            if st["hours"].get(key, {}).get("status") == "http_404":
                continue
            while stop is None:
                stop = budget.left() or ("max_hours" if time.time() > t_end else None)
                if stop:
                    break
                try:
                    status, body = fetch(dk.url(SYMBOL, h), budget)
                except BudgetSpent as e:
                    stop = str(e)
                    break
                except (RateLimited, NetworkError) as e:
                    limited = isinstance(e, RateLimited)
                    wait = e.retry_after if limited and e.retry_after is not None else args.wait
                    if not limited:
                        fails = st["hours"].get(key, {}).get("failures", 0) + 1
                        st["hours"][key] = {"status": "unresolved", "failures": fails, "error": str(e)[-200:],
                                            "at": now_iso()}
                        if fails >= args.max_failures:
                            stop = f"{fails} network failures in a row at {key}"
                    st["rate_limits"].append({"at": now_iso(), "hour": key, "kind": "http_429" if limited else "network",
                                              "retry_after": e.retry_after if limited else None, "waited_s": wait})
                    save_status(args.month, st)
                    if stop:
                        break
                    try:
                        budget.wait(wait)
                    except BudgetSpent as be:
                        stop = str(be)
                        break
                    continue
                if status == 404:
                    st["hours"][key] = {"status": "http_404", "at": now_iso()}
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(body)
                    st["hours"][key] = {"status": "file" if body else "empty_response", "bytes": len(body),
                                        "at": now_iso()}
                    got += 1
                save_status(args.month, st)
                time.sleep(args.pause)
                break
            if stop:
                break
        missing = sum(1 for h in dk.month_hours(y, m) if not raw_path(h).exists()
                      and st["hours"].get(h.isoformat(), {}).get("status") != "http_404")
        st["runs"].append({"at": now_iso(), "downloaded": got, "stopped": stop, "hours_missing": missing,
                           "budget": {k: b[k] for k in ("requests", "max_requests", "wait_seconds", "max_wait_seconds")}})
        save_status(args.month, st)
    print(f"{args.month}: downloaded {got} this run; hours still missing {missing}; "
          f"budget {b['requests']}/{b['max_requests']} requests, {b['wait_seconds']}/{b['max_wait_seconds']} s waited"
          + (f"; stopped: {stop}" if stop else ""))
    if stop and stop != "max_hours":
        raise SystemExit(f"{args.month}: partial - {missing} hours missing ({stop})")


def load_month(key: str):
    """Decoded ticks of a month in hour order, plus one row per hour with its download status, read from the
    current files (never from an earlier manifest)."""
    y, m = check_month(key)
    st = load_status(key)["hours"]
    frames, files = [], []
    for h in dk.month_hours(y, m):
        p = raw_path(h)
        if not p.exists():
            files.append({"hour": h.isoformat(), "file": None,
                          "status": st.get(h.isoformat(), {}).get("status", "not_downloaded")})
            continue
        data = p.read_bytes()
        df = dk.decode(data, h, dk.PRICE_DIVISOR[SYMBOL])
        frames.append(df)
        files.append({"hour": h.isoformat(), "file": p.relative_to(RAW).as_posix(), "bytes": len(data),
                      "sha256": sha256(data), "ticks": int(len(df)),
                      "status": "file_with_ticks" if len(df) else "empty_response"})
    ticks = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=dk.COLUMNS)
    return ticks, files


NEW_YORK = ZoneInfo("America/New_York")
SCHEDULE_BASIS = ("spot gold (XAU/USD) schedule in New York time: weekly close Friday 17:00 to Sunday 18:00, "
                  "daily break 17:00-18:00 Monday-Thursday; holidays are not in it")


def schedule_closed(hour: dt.datetime) -> bool:
    """The whole UTC hour [hour, hour+1h) lies in the spot-gold closed schedule (New York time, so daylight saving
    moves it in UTC). Holidays are not part of the schedule: they are never assumed closed."""
    for minute in range(0, 60, 5):
        t = (hour + dt.timedelta(minutes=minute)).astimezone(NEW_YORK)
        wd, hm = t.weekday(), t.hour * 60 + t.minute
        weekend = (wd == 4 and hm >= 17 * 60) or wd == 5 or (wd == 6 and hm < 18 * 60)
        daily = wd in (0, 1, 2, 3) and 17 * 60 <= hm < 18 * 60
        if not (weekend or daily):
            return False
    return True


def classify_hours(files: list) -> list:
    """Each hour's coverage: 'ticks' (a file with ticks), 'closed_verified' (no ticks, a resolved answer - empty
    file or 404 - and the whole hour inside the closed schedule), 'pending' (not downloaded or unresolved), or
    'unverified' (a resolved answer without ticks outside that schedule, e.g. a holiday or a missing trading
    hour). A tick gap alone never makes an hour 'closed'."""
    out = []
    for f in files:
        s = f["status"]
        if s == "file_with_ticks":
            c = "ticks"
        elif s in ("not_downloaded", "unresolved"):
            c = "pending"
        elif s in ("empty_response", "http_404"):
            c = "closed_verified" if schedule_closed(dt.datetime.fromisoformat(f["hour"])) else "unverified"
        else:
            c = "unverified"
        out.append({**f, "coverage": c})
    return out


def coverage(month: str) -> dict:
    """Recomputed from the current files: download_resolved (every hour answered: a file, an empty answer or a
    404) and coverage_confirmed (every hour has ticks or is a verified closed hour)."""
    ticks, files = load_month(month)
    rows = classify_hours(files)
    counts = pd.Series([r["coverage"] for r in rows]).value_counts().to_dict()
    present = [f for f in files if f["file"]]
    return {"ticks": ticks, "rows": rows, "counts": counts,
            "download_resolved": counts.get("pending", 0) == 0,
            "coverage_confirmed": counts.get("pending", 0) == 0 and counts.get("unverified", 0) == 0,
            "sha256_of_file_list": sha256("".join(f"{f['file']}:{f['sha256']}\n" for f in present).encode())}


def cmd_manifest(args) -> None:
    cov = coverage(args.month)
    ticks, rows = cov["ticks"], cov["rows"]
    present = [f for f in rows if f["file"]]
    st = load_status(args.month)
    man = {"source": "Dukascopy historical data feed (free), " + dk.BASE + "/XAUUSD/<YYYY>/<MM-1>/<DD>/<HH>h_ticks.bi5",
           "format": "LZMA; 20-byte big-endian records: uint32 ms since the UTC hour, uint32 ask, uint32 bid, "
                     "float32 ask volume, float32 bid volume; price = integer / 1000",
           "symbol": SYMBOL, "month": args.month, "time_zone": "UTC (hour in the path, ms offset in the record)",
           "raw_location": "outside git (NWQ_DUKA_DIR, default D:/data/dukascopy)",
           "download_resolved": cov["download_resolved"], "coverage_confirmed": cov["coverage_confirmed"],
           "closed_schedule_basis": SCHEDULE_BASIS,
           "hours_in_month": len(rows), "hours_by_download_status": pd.Series([r["status"] for r in rows]).value_counts().to_dict(),
           "hours_by_coverage": cov["counts"],
           "unverified_hours": [r["hour"] for r in rows if r["coverage"] == "unverified"],
           "pending_hours": [r["hour"] for r in rows if r["coverage"] == "pending"],
           "files": len(present), "bytes": sum(f["bytes"] for f in present), "ticks": int(len(ticks)),
           "first_tick_utc": _iso(ticks["time_msc"].min()) if len(ticks) else None,
           "last_tick_utc": _iso(ticks["time_msc"].max()) if len(ticks) else None,
           "rate_limit_events": len(st["rate_limits"]), "rate_limits_observed": st["rate_limits"],
           "download_budget": load_budget(),
           "sha256_of_file_list": cov["sha256_of_file_list"], "per_hour": rows}
    evaluate.save(man, RESULTS / f"manifest_{args.month}.json")
    print(json.dumps({k: v for k, v in man.items() if k not in ("per_hour", "rate_limits_observed",
                                                              "unverified_hours", "pending_hours")}, indent=1))


def require_resolved(month: str, allow_partial: bool) -> dict:
    """The coverage of the current files, checked against the saved manifest (it must describe these very files).
    A month with pending hours is refused unless a labelled partial check is asked for."""
    man_p = RESULTS / f"manifest_{month}.json"
    if not man_p.exists():
        raise SystemExit(f"run manifest {month} first")
    cov = coverage(month)
    if json.loads(man_p.read_text())["sha256_of_file_list"] != cov["sha256_of_file_list"]:
        raise SystemExit(f"{month}: the manifest does not describe the current files; run manifest {month} again")
    if not cov["download_resolved"] and not allow_partial:
        raise SystemExit(f"{month}: {cov['counts'].get('pending', 0)} hours not downloaded - the month is partial "
                         "(--partial only for a labelled partial check)")
    return cov


def sample_ticks() -> pd.DataFrame:
    p = raw_path(SAMPLE_HOUR)
    if not p.exists():
        raise SystemExit(f"{p} missing: the sample hour's feed file is needed")
    return dk.decode(p.read_bytes(), SAMPLE_HOUR, dk.PRICE_DIVISOR[SYMBOL])


def source_ticks(key: str) -> pd.DataFrame:
    """The source ticks of a month (current files) or of the one-hour sample."""
    return sample_ticks() if key == SAMPLE else load_month(key)[0]


def cmd_manual_check(args) -> None:
    """The manual web export of the sample hour against the feed file: layout, both sides, pairing, row by row."""
    out = {"source": "Dukascopy Historical Data Export widget (manual download by the user), period 1 Tick, "
                     "one file per offer side",
           "location": "outside git (data/ in the working copy, gitignored)", "files": {}}
    sides = {}
    for side, name in MANUAL_FILES.items():
        p = MANUAL / name
        data = p.read_bytes()
        df = dk.read_export_csv(p)
        sides[side] = df
        out["files"][side] = {"name": name, "bytes": len(data), "sha256": sha256(data), "rows": int(len(df)),
                              "first_utc": _iso(int(df["time_s"].min()) * 1000),
                              "last_utc": _iso(int(df["time_s"].max()) * 1000),
                              "time_nondecreasing": bool((np.diff(df["time_s"].to_numpy()) >= 0).all()),
                              "price_min": float(df["price"].min()), "price_max": float(df["price"].max())}
    pairs = dk.pair_export_sides(sides["ASK"], sides["BID"])
    spread = pairs["ask"] - pairs["bid"]
    out["pairing"] = {"method": "row position (the time is equal on every row; never joined by time)",
                      "rows": int(len(pairs)), "crossed": int((spread < 0).sum()),
                      "spread_min": round(float(spread.min()), 3), "spread_max": round(float(spread.max()), 3),
                      "rows_sharing_a_second": int(pd.Series(pairs["time_s"]).duplicated(keep=False).sum())}
    out["against_feed_hour"] = {"feed_file": raw_path(SAMPLE_HOUR).relative_to(RAW).as_posix(),
                                **dk.compare_export_to_feed(pairs, sample_ticks())}
    evaluate.save(out, RESULTS / "manual_sample_check.json")
    print(json.dumps(out, indent=1))


def cmd_export_sample(args) -> None:
    ticks = sample_ticks()
    if dk.quality(ticks)["out_of_order"]:
        raise SystemExit("sample: ticks out of time order - not exported")
    cfg = env.load_config()
    frm = int(SAMPLE_HOUR.timestamp() * 1000)
    dst = mt5_files(cfg) / bin_name(SAMPLE)
    write_ticks_bin(dst, ticks, frm, frm + 3_600_000 - 1)
    print(f"{dst}: {len(ticks)} ticks, {dst.stat().st_size} bytes, sha256 {sha256(dst.read_bytes())}")


# ------------------------------------------------------------------ quality
def derived(name: str) -> pathlib.Path:
    p = RAW / "derived" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def cmd_quality(args) -> None:
    cov = require_resolved(args.month, args.partial)
    ticks, files = cov["ticks"], cov["rows"]
    q = dk.quality(ticks)
    g = dk.gaps(ticks, min_seconds=300, holidays=HOLIDAYS)
    kinds = pd.Series([x["kind"] for x in g]).value_counts().to_dict() if g else {}
    dens = dk.density(ticks)
    m1, m5 = dk.bars(ticks, 60), dk.bars(ticks, 300)
    m1.to_csv(derived(f"bars_m1_{args.month}.csv.gz"), index=False)
    m5.to_csv(derived(f"bars_m5_{args.month}.csv.gz"), index=False)
    day = pd.to_datetime(ticks["time_msc"], unit="ms", utc=True).dt.date
    daily = pd.DataFrame({"day": day, "spread": ticks["ask"] - ticks["bid"]}).groupby("day").agg(
        ticks=("spread", "size"), spread_median=("spread", "median"), spread_p99=("spread", lambda s: s.quantile(0.99)))
    sec = ticks["time_msc"].to_numpy() // 1000
    per_sec = pd.Series(sec).value_counts()
    out = {"month": args.month, "partial_sample": not cov["download_resolved"],
           "coverage_confirmed": cov["coverage_confirmed"], "hours_by_coverage": cov["counts"], "quality": q,
           "gaps_over_5min": {"count": len(g), "by_kind": kinds, "list": g},
           "bars": {"m1": int(len(m1)), "m5": int(len(m5)),
                    "m1_ticks_median": float(m1["ticks"].median()), "m1_ticks_p01": float(m1["ticks"].quantile(0.01)),
                    "m1_single_tick_bars": int((m1["ticks"] == 1).sum()),
                    "m1_bid_range_max": round(float((m1["high"] - m1["low"]).max()), 3)},
           "ticks_per_second": {"max": int(per_sec.max()) if len(per_sec) else 0,
                                "seconds_with_ticks": int(len(per_sec))},
           "daily": [{"day": str(k), **{c: round(float(v), 4) for c, v in r.items()}} for k, r in daily.iterrows()],
           "hours_by_status": pd.Series([f["status"] for f in files]).value_counts().to_dict()}
    evaluate.save(out, RESULTS / f"quality_{args.month}.json")
    dens.to_csv(RESULTS / f"density_{args.month}.csv", index=False)
    print(json.dumps({k: v for k, v in out.items() if k not in ("daily",)} | {"gaps_over_5min": {"count": len(g), "by_kind": kinds}},
                     indent=1, default=str)[:4000])


# ------------------------------------------------------------------ MT5 import (offline, limited exception)
def mt5_files(cfg) -> pathlib.Path:
    return cfg.mt5_dir / "MQL5" / "Files"


def bin_name(key: str) -> str:
    return f"duka_XAUUSD_{key}.bin"


def write_ticks_bin(path: pathlib.Path, ticks: pd.DataFrame, from_msc: int, to_msc: int) -> None:
    """Header int64 from_msc, to_msc, n; then n x (int64 time_msc, double bid, double ask), little-endian."""
    rec = np.zeros(len(ticks), dtype=[("t", "<i8"), ("b", "<f8"), ("a", "<f8")])
    rec["t"], rec["b"], rec["a"] = ticks["time_msc"].to_numpy(), ticks["bid"].to_numpy(), ticks["ask"].to_numpy()
    with open(path, "wb") as f:
        f.write(struct.pack("<qqq", from_msc, to_msc, len(rec)))
        f.write(rec.tobytes())


def read_ticks_bin(path: pathlib.Path) -> tuple:
    with open(path, "rb") as f:
        frm, to, n = struct.unpack("<qqq", f.read(24))
        rec = np.frombuffer(f.read(), dtype=[("t", "<i8"), ("b", "<f8"), ("a", "<f8")], count=n)
    return frm, to, pd.DataFrame({"time_msc": rec["t"], "bid": rec["b"], "ask": rec["a"]})


def month_range_msc(month: str) -> tuple:
    hs = dk.month_hours(*check_month(month))
    return int(hs[0].timestamp() * 1000), int(hs[-1].timestamp() * 1000) + 3_600_000 - 1


def cmd_export(args) -> None:
    ticks = require_resolved(args.month, False)["ticks"]
    q = dk.quality(ticks)
    if q["out_of_order"]:
        raise SystemExit(f"{args.month}: {q['out_of_order']} ticks out of time order - not exported; decide first")
    cfg = env.load_config()
    frm, to = month_range_msc(args.month)
    dst = mt5_files(cfg) / bin_name(args.month)
    write_ticks_bin(dst, ticks, frm, to)
    print(f"{dst}: {len(ticks)} ticks, {dst.stat().st_size} bytes, sha256 {sha256(dst.read_bytes())}")


def offline_lines(server: str) -> list:
    """No account (Login=0) and every connection through a proxy on a closed local port, so nothing can connect;
    algorithmic trading off. The server name only selects the local symbol base."""
    return ["[Common]", f"Server={server}", "Login=0", "ProxyEnable=1", "ProxyType=0",
            "ProxyAddress=127.0.0.1:9", "NewsEnable=0",
            "[Experts]", "AllowLiveTrading=0", "AllowDllImport=0", "Enabled=0"]


def startup_ini(server: str, script_parameters: str = "") -> str:
    """Offline start (offline_lines) with the import script at startup (with a preset from MQL5/Presets when
    given); the terminal shuts down after it. With an unknown server (first attempt, 2026-10-05) the chart symbol
    did not exist; with the local base (second attempt) the script still timed out (300 s) before OnStart."""
    return "\r\n".join(offline_lines(server) + ["[StartUp]", f"Script={pathlib.Path(IMPORT_SCRIPT).stem}"]
                       + ([f"ScriptParameters={script_parameters}"] if script_parameters else [])
                       + ["ShutdownTerminal=1", ""])


def gui_ini(server: str) -> str:
    """The user's manual session in the terminal's interface: offline_lines only, no startup program."""
    return "\r\n".join(offline_lines(server) + [""])


TRADE_CALLS = ("OrderSend", "OrderSendAsync", "PositionOpen", "PositionClose", "PositionModify", "CTrade",
               "Trade.mqh", "OrderCalcProfit")


def assert_no_trading_calls(src: pathlib.Path) -> None:
    text = src.read_text(encoding="utf-8")
    hits = [c for c in TRADE_CALLS if c in text]
    if hits:
        raise SystemExit(f"{src.name} contains trading calls {hits}; refusing")


def run_part(data: bytes, start: int) -> str:
    """The text a run appended to a terminal log (UTF-16 with BOM) after it was ``start`` bytes long: the day's
    log holds earlier runs too, and their lines must not be read as this run's."""
    start = max(2, start - start % 2) if data[:2] == b"\xff\xfe" else start
    tail = data[start:]
    return tail.decode("utf-16-le", "replace") if data[:2] == b"\xff\xfe" else tail.decode("utf-8", "replace")


def connection_lines(journal: str) -> list:
    """Lines saying the terminal connected or authorized, with every long number (an account) masked."""
    hits = [l for l in journal.splitlines() if any(w in l.lower() for w in ("authorized on", "connected to"))]
    return [re.sub(r"\d{5,}", "<number>", l) for l in hits]


def cmd_import(args) -> None:
    """The CLAUDE.md limited exception: one offline start of C:\\mt5r that runs the import script, nothing else."""
    if runner.live_terminal_running():
        raise SystemExit("live MT5 terminal is running; please close it first (it is never closed by this tool)")
    cfg = env.load_config()
    env.assert_isolated(cfg)
    if runner.isolated_processes(cfg):
        raise SystemExit("isolated terminal already running")
    src = SRC_DIR / IMPORT_SCRIPT
    assert_no_trading_calls(src)
    env.disable_mcp(cfg)
    env.assert_trade_safety(cfg)
    keys = [SAMPLE] if args.sample else list(MONTHS)
    for m in keys:
        if not (mt5_files(cfg) / bin_name(m)).exists():
            raise SystemExit(f"{bin_name(m)} missing in MQL5/Files: run "
                             f"{'export-sample' if m == SAMPLE else 'export ' + m} first")
    preset = ""
    if args.sample:                                     # only the sample file; the script's default lists the months
        preset = "duka_import_sample.set"
        (cfg.mt5_dir / "MQL5" / "Presets").mkdir(parents=True, exist_ok=True)
        env.write_utf16(cfg.mt5_dir / "MQL5" / "Presets" / preset,
                        f"CustomName={CUSTOM}\r\nTickFiles={bin_name(SAMPLE)}\r\n")
    scripts = cfg.mt5_dir / "MQL5" / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, scripts / IMPORT_SCRIPT)
    comp = compmod.compile_ea(cfg, IMPORT_SCRIPT, folder="Scripts")
    if comp["errors"] != 0 or not comp["ex5_exists"]:
        raise SystemExit(f"compile failed: {comp['log'][-1500:]}")
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    keep = RAW / "mt5_import" / stamp
    keep.mkdir(parents=True)
    common = cfg.mt5_dir / "config" / "common.ini"
    backup = keep / "common.ini.before"
    shutil.copy2(common, backup)
    log_file = mt5_files(cfg) / "duka_import_log.txt"
    if log_file.exists():
        log_file.unlink()
    ini_rel = pathlib.Path("runs_ini") / "duka_import.ini"
    env.write_utf16(cfg.mt5_dir / ini_rel, startup_ini(cfg.server, preset))
    log_sizes = {f.name: f.stat().st_size for f in cfg.mt5_dir.glob("logs/*.log")}
    t0 = time.time()
    cmd = cfg.launcher() + [cfg.win_path("terminal64.exe"), "/portable", f"/config:{cfg.win_path(ini_rel.as_posix())}"]
    proc = subprocess.Popen(cmd, env=cfg.env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    status = "ok"
    try:
        proc.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        status = "timeout"
        runner.shutdown_prefix(cfg)
    deadline = time.time() + 120
    while runner.isolated_processes(cfg) and time.time() < deadline:
        time.sleep(2)
    restored = common.read_bytes() != backup.read_bytes()
    shutil.copy2(backup, common)                        # the account settings exactly as before
    env.disable_mcp(cfg)
    env.assert_trade_safety(cfg)
    journal = ""
    for f in cfg.mt5_dir.glob("logs/*.log"):
        if f.stat().st_mtime >= t0 - 5:
            shutil.copy2(f, keep / f.name)
            journal += run_part(f.read_bytes(), log_sizes.get(f.name, 0))
    (keep / "journal_this_run.txt").write_text(journal, encoding="utf-8")
    connected = connection_lines(journal)
    log_text = log_file.read_text(encoding="latin-1") if log_file.exists() else ""
    if log_file.exists():
        shutil.copy2(log_file, keep / log_file.name)
    out = {"scope": f"sample hour {SAMPLE_HOUR.isoformat()}" if args.sample else "months " + ", ".join(MONTHS),
           "status": status, "seconds": round(time.time() - t0, 1),
           "script_result": "ok" if "result: ok" in log_text else ("not_started" if not log_text else "mismatch"),
           "common_ini_changed_by_terminal": restored,
           "common_ini_restored": True, "account_connection_lines": connected, "script_log": log_text.splitlines(),
           "journal_kept_in": keep.as_posix()}
    evaluate.save(out, RESULTS / ("import_run_sample.json" if args.sample else "import_run.json"))
    print(json.dumps(out, indent=1))
    loaded = all(f"{bin_name(m)}: readback" in log_text for m in keys)
    if connected or "result: ok" not in log_text or not loaded:
        raise SystemExit("import not clean - see import_run.json")


# ------------------------------------------------------------------ manual import in the interface (user, 2026-10-05)
MANUAL_IMPORT = RAW / "mt5_manual"
MT5_TICK_HEADER = ["<DATE>", "<TIME>", "<BID>", "<ASK>", "<LAST>", "<VOLUME>"]
GUI_INI = pathlib.Path("runs_ini") / "duka_gui.ini"
GUI_DIR = RAW / "mt5_gui"


def _price(raw: int) -> str:
    return f"{raw // 1000}.{raw % 1000:03d}"


def mt5_tick_lines(ticks: pd.DataFrame) -> list:
    """The terminal's tick import format (Symbols > Ticks > Import Ticks): tab-separated Date (YYYY.MM.DD), Time
    (HH:MM:SS.mmm), Bid, Ask, Last, Volume; one line per feed tick, in feed order. Times are the feed's UTC
    milliseconds unchanged; prices are the feed's integers written with exactly 3 decimals (no float rounding).
    Last and Volume are 0: the feed has quote volumes, not trades, and the terminal skips values <= 0. No flags:
    the terminal computes them."""
    out = ["\t".join(MT5_TICK_HEADER)]
    for t, b, a in zip(ticks["time_msc"].astype("int64"), ticks["raw_bid"].astype("int64"),
                       ticks["raw_ask"].astype("int64")):
        d = dt.datetime.fromtimestamp(int(t) // 1000, dt.timezone.utc)
        out.append(f"{d:%Y.%m.%d}\t{d:%H:%M:%S}.{int(t) % 1000:03d}\t{_price(int(b))}\t{_price(int(a))}\t0.000\t0")
    return out


def read_mt5_tick_lines(text: str) -> pd.DataFrame:
    """Back from the import format: time_msc (UTC as written), bid, ask, in file order."""
    rows = [l.split("\t") for l in text.splitlines() if l.strip()]
    if rows[0] != MT5_TICK_HEADER:
        raise ValueError(f"header {rows[0]} is not the import layout")
    t = [int(dt.datetime.strptime(f"{r[0]} {r[1]}", "%Y.%m.%d %H:%M:%S.%f").replace(tzinfo=dt.timezone.utc)
             .timestamp() * 1000 + 0.5) for r in rows[1:]]
    return pd.DataFrame({"time_msc": t, "bid": [float(r[2]) for r in rows[1:]], "ask": [float(r[3]) for r in rows[1:]]})


SPANS = {"sample": (SAMPLE_HOUR, SAMPLE_HOUR + dt.timedelta(hours=1)),
         # every downloaded hour with ticks, contiguous: the market reopens 2024-01-01 23:00 UTC (the hours before
         # are empty answers), 2024-01-02 22:00 is the daily break (empty answer)
         "span0102": (dt.datetime(2024, 1, 1, 23, tzinfo=dt.timezone.utc), dt.datetime(2024, 1, 3, tzinfo=dt.timezone.utc))}


CRLF = chr(13) + chr(10)


def span_hours(name: str) -> list:
    a, b = SPANS[name]
    return [h for h in dk.month_hours(a.year, a.month) if a <= h < b]


def span_ticks(name: str) -> tuple:
    """The feed ticks of a span, hour by hour in order, and the source files (a missing file is refused)."""
    frames, files = [], []
    for h in span_hours(name):
        p = raw_path(h)
        if not p.exists():
            raise SystemExit(f"{p} missing: the span is not covered by downloaded files")
        data = p.read_bytes()
        df = dk.decode(data, h, dk.PRICE_DIVISOR[SYMBOL])
        frames.append(df)
        files.append({"file": p.relative_to(RAW).as_posix(), "bytes": len(data), "sha256": sha256(data),
                      "ticks": int(len(df))})
    return pd.concat(frames, ignore_index=True), files


def manual_import_path(name: str = "sample") -> pathlib.Path:
    if name == "sample":
        return MANUAL_IMPORT / f"{CUSTOM}_{SAMPLE_HOUR:%Y%m%d_%H}00_UTC_ticks.csv"
    a, b = SPANS[name]
    return MANUAL_IMPORT / f"{CUSTOM}_{a:%Y%m%d_%H%M}_to_{b:%Y%m%d_%H%M}_UTC_ticks.csv"


def cmd_manual_import_file(args) -> None:
    """A span's feed ticks as a file for the user's manual import, outside git, never overwritten with different
    content; a manifest next to it and in the results folder."""
    ticks, files = span_ticks(args.span)
    a, b = SPANS[args.span]
    data = (CRLF.join(mt5_tick_lines(ticks)) + CRLF).encode("ascii")
    dst = manual_import_path(args.span)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.read_bytes() != data:
        raise SystemExit(f"{dst} exists with other content; never overwritten")
    if not dst.exists():
        dst.write_bytes(data)
    back = read_mt5_tick_lines(dst.read_text(encoding="ascii"))
    d = np.diff(ticks["time_msc"].to_numpy())
    manual = {s: sha256((MANUAL / n).read_bytes()) for s, n in MANUAL_FILES.items() if (MANUAL / n).exists()}
    man = {"purpose": "import path and data fidelity check; not a month's coverage", "span": args.span,
           "source": {"kind": "Dukascopy free historical feed files (LZMA .bi5), downloaded earlier",
                      "files": files} if args.span != "sample" else
                     {"kind": "Dukascopy free historical feed file (LZMA .bi5), downloaded earlier", **files[0]},
           "file": {"name": dst.name, "location": "outside git: NWQ_DUKA_DIR/mt5_manual (default D:/data/dukascopy)",
                    "bytes": len(data), "sha256": sha256(data),
                    "format": "ASCII, CRLF, tab-separated, header <DATE> <TIME> <BID> <ASK> <LAST> <VOLUME>; "
                              "date YYYY.MM.DD, time HH:MM:SS.mmm; prices with 3 decimals; LAST 0.000 and VOLUME 0 "
                              "(no trade prices or trade volumes in the source); no flags column",
                    "time_zone": "UTC as in the feed (the terminal stores times without a zone)"},
           "ticks": int(len(ticks)), "first_tick_utc": _iso(int(ticks["time_msc"].min())),
           "last_tick_utc": _iso(int(ticks["time_msc"].max())),
           "period": {"from_utc": a.isoformat(), "to_utc": b.isoformat() + " (exclusive)"},
           "order": "feed order, unchanged", "same_ms_as_previous": int((d == 0).sum()),
           "out_of_order": int((d < 0).sum()),
           "nothing_removed_or_added": "every feed record is one line; duplicates kept; no time invented",
           "written_file_read_back_vs_feed": compare_ticks(ticks, back, "file written vs feed ticks"),
           "manual_web_export_sha256_unchanged": manual}
    res = (RESULTS / "manual_import_file_manifest.json" if args.span == "sample"
           else RESULTS2 / f"manual_import_file_manifest_{args.span}.json")
    if res.exists():                                    # committed evidence is never overwritten
        print(f"{res} exists; kept unchanged")
    else:
        evaluate.save(man, res)
    (dst.parent / (dst.stem + "_manifest.json")).write_text(json.dumps(man, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in man.items() if k != "source"}, indent=1))


def cmd_gui_prepare(args) -> None:
    """Before the user's manual session: safety checks, a backup of common.ini, the offline start config and the
    journal positions. The terminal is started by the user, never by this tool."""
    if runner.live_terminal_running():
        raise SystemExit("live MT5 terminal is running; please close it first (it is never closed by this tool)")
    cfg = env.load_config()
    env.assert_isolated(cfg)
    if runner.isolated_processes(cfg):
        raise SystemExit("isolated terminal already running")
    env.disable_mcp(cfg)
    env.assert_trade_safety(cfg)
    if (GUI_DIR / "open_session.json").exists():
        raise SystemExit("a manual session is already prepared; run gui-finish first")
    if (cfg.mt5_dir / "bases" / "symbols.custom.dat").exists():
        cmd_custom_backup(args)                         # the custom symbol as it is, before the user changes it
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    keep = GUI_DIR / stamp
    keep.mkdir(parents=True)
    shutil.copy2(cfg.mt5_dir / "config" / "common.ini", keep / "common.ini.before")
    env.write_utf16(cfg.mt5_dir / GUI_INI, gui_ini(cfg.server))
    state = {"stamp": stamp, "prepared_at": now_iso(), "t0": time.time(),
             "log_sizes": {f.name: f.stat().st_size for f in cfg.mt5_dir.glob("logs/*.log")}}
    (GUI_DIR / "open_session.json").write_text(json.dumps(state), encoding="utf-8")
    print("start the isolated terminal yourself with:")
    print(f'"{cfg.win_path("terminal64.exe")}" /portable /config:{cfg.win_path(GUI_INI.as_posix())}')


def cmd_gui_finish(args) -> None:
    """After the user closed the terminal: restore common.ini, check this session's journal (connections masked),
    and record which custom-symbol files exist. Import success is judged later from readback and Tester ticks."""
    st_p = GUI_DIR / "open_session.json"
    if not st_p.exists():
        raise SystemExit("no prepared manual session")
    cfg = env.load_config()
    if runner.isolated_processes(cfg):
        raise SystemExit("the isolated terminal is still running; close it first")
    state = json.loads(st_p.read_text(encoding="utf-8"))
    keep = GUI_DIR / state["stamp"]
    common = cfg.mt5_dir / "config" / "common.ini"
    changed = common.read_bytes() != (keep / "common.ini.before").read_bytes()
    shutil.copy2(keep / "common.ini.before", common)
    env.disable_mcp(cfg)
    env.assert_trade_safety(cfg)
    journal = ""
    for f in cfg.mt5_dir.glob("logs/*.log"):
        if f.stat().st_mtime >= state["t0"] - 5:
            shutil.copy2(f, keep / f.name)
            journal += run_part(f.read_bytes(), state["log_sizes"].get(f.name, 0))
    (keep / "journal_this_session.txt").write_text(journal, encoding="utf-8")
    words = ("custom", CUSTOM.lower(), "import", "tick")
    relevant = [re.sub(r"\d{5,}", "<number>", l) for l in journal.splitlines() if any(w in l.lower() for w in words)]
    base = cfg.mt5_dir / "bases" / "Custom"
    files = sorted((p.relative_to(base).as_posix(), p.stat().st_size) for p in base.rglob("*") if p.is_file())
    out = {"session": state["stamp"], "prepared_at": state["prepared_at"], "finished_at": now_iso(),
           "common_ini_changed_by_terminal": changed, "common_ini_restored": True,
           "account_connection_lines": connection_lines(journal),
           "journal_lines_custom_or_import": relevant[:200], "journal_lines_total": len(journal.splitlines()),
           "custom_base_files": [{"path": p, "bytes": b} for p, b in files],
           "journal_kept_in": keep.as_posix()}
    evaluate.save(out, RESULTS / "gui_session.json")
    st_p.rename(keep / "session_state.json")
    print(json.dumps(out, indent=1))
    if out["account_connection_lines"]:
        raise SystemExit("the session connected to a server - see gui_session.json")


def cmd_verify_import(args) -> None:
    cfg = env.load_config()
    src = source_ticks(args.month)
    frm, to, back = read_ticks_bin(mt5_files(cfg) / ("readback_" + bin_name(args.month)))
    out = compare_ticks(src, back, "terminal readback")
    evaluate.save(out, RESULTS / f"import_check_{args.month}.json")
    print(json.dumps(out, indent=1))


def compare_ticks(src: pd.DataFrame, got: pd.DataFrame, label: str) -> dict:
    """Tick-by-tick: counts, then the multiset of (time_msc, bid, ask) at 3 decimals - extra ticks (generated) and
    missing ticks (dropped) are listed separately."""
    key = lambda d: pd.DataFrame({"t": d["time_msc"].astype("int64").to_numpy(),
                                  "b": np.round(d["bid"].astype(float).to_numpy() * 1000).astype("int64"),
                                  "a": np.round(d["ask"].astype(float).to_numpy() * 1000).astype("int64")})
    a, b = key(src), key(got)
    ca = a.value_counts().rename("n_src")
    cb = b.value_counts().rename("n_got")
    j = pd.concat([ca, cb], axis=1).fillna(0)
    extra = j[j["n_got"] > j["n_src"]]
    missing = j[j["n_src"] > j["n_got"]]
    same_order = len(a) == len(b) and bool((a.to_numpy() == b.to_numpy()).all())
    ex = lambda d: [{"time": _iso(t), "bid": bb / 1000, "ask": aa / 1000} for (t, bb, aa) in list(d.index)[:10]]
    return {"compared": label, "ticks_source": int(len(a)), "ticks_target": int(len(b)),
            "identical_in_order": same_order,
            "extra_in_target": int((extra["n_got"] - extra["n_src"]).sum()),
            "missing_in_target": int((missing["n_src"] - missing["n_got"]).sum()),
            "first_extra": ex(extra), "first_missing": ex(missing)}


# ------------------------------------------------------------------ Tester tick dump (never trades)
def dump_run_id(symbol: str, month: str) -> str:
    return f"duka_dump_{'duka' if symbol == CUSTOM else 'bybit'}_{month.replace('-', '')}"


def cmd_tester_dump(args) -> None:
    if args.month == SAMPLE and args.symbol != CUSTOM:
        raise SystemExit(f"the sample is {CUSTOM} only")
    if args.symbol not in (CUSTOM, BYBIT):
        raise SystemExit(f"symbol must be {CUSTOM} or {BYBIT}")
    if args.symbol == BYBIT and args.month != "2026-03":
        raise SystemExit("the Bybit comparison is March 2026 only")
    cfg = env.load_config()
    shutil.copy2(SRC_DIR / DUMP_EA, cfg.mt5_dir / "MQL5" / "Experts" / DUMP_EA)
    comp = compmod.compile_ea(cfg, DUMP_EA)
    if comp["errors"] != 0 or not comp["ex5_exists"]:
        raise SystemExit(f"compile failed: {comp['log'][-1500:]}")
    hs = [SAMPLE_HOUR] if args.month == SAMPLE else dk.month_hours(*check_month(args.month))
    rid = dump_run_id(args.symbol, args.month) + ("_rb" if args.readback else "")
    lines = [f"ResearchRunTag={rid}"]
    if args.readback:               # the stored ticks of the period, read at init (the Tester may deliver none)
        frm = int(hs[0].timestamp() * 1000)
        lines += [f"ReadbackFromMsc={frm}", f"ReadbackToMsc={int(hs[-1].timestamp() * 1000) + 3_600_000 - 1}"]
    text = ini.render(expert=pathlib.Path(DUMP_EA).stem + ".ex5", symbol=args.symbol, period="M1",
                      from_date=hs[0].strftime("%Y.%m.%d"), to_date_inclusive=hs[-1].strftime("%Y.%m.%d"),
                      deposit=10000, report=f"reports\\{rid}", set_lines=lines)
    res = runner.run(cfg, rid, text, pathlib.Path(DUMP_EA).stem + ".ex5", meta={"role": "duka_data_check"})
    print(rid, res.status, res.seconds)


def tester_ticks(rid: str) -> pd.DataFrame:
    return pd.read_csv(runner.RUNS / rid / f"rl_ticks_{rid}.csv")


# ------------------------------------------------------------------ v2: backup, diagnostics, OnTick check (2026-10-05)
RESULTS2 = REPO / "results" / "dukascopy_v2"


def cmd_custom_backup(args) -> None:
    """A copy of the isolated copy's custom-symbol definition and data (bases/symbols.custom.dat, bases/Custom)
    outside git, with sha256 per file, before any change to the symbol. Reads only; refuses while a terminal runs."""
    cfg = env.load_config()
    if runner.isolated_processes(cfg):
        raise SystemExit("the isolated terminal is running; close it first")
    base = cfg.mt5_dir / "bases"
    srcs = [base / "symbols.custom.dat"] + sorted(p for p in (base / "Custom").rglob("*") if p.is_file())
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    keep = RAW / "mt5_backup" / stamp
    files = []
    for p in srcs:
        rel = p.relative_to(base)
        dst = keep / "bases" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
        h, h2 = sha256(p.read_bytes()), sha256(dst.read_bytes())
        if h != h2:
            raise SystemExit(f"{rel}: copy differs from the source")
        files.append({"path": "bases/" + rel.as_posix(), "bytes": p.stat().st_size, "sha256": h,
                      "modified": dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")})
    out = {"backup": stamp, "location": "outside git: NWQ_DUKA_DIR/mt5_backup/<stamp>", "files": files,
           "restore": "with every terminal closed, copy the saved bases/ files back over C:/mt5r/bases"}
    (keep / "backup_manifest.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    evaluate.save(out, RESULTS2 / f"backup_{stamp}.json")
    print(json.dumps(out, indent=1))


def probe_run_id(tag: str) -> str:
    return f"duka_probe_{tag}"


def cmd_tester_probe(args) -> None:
    """The diagnostic EA (never trades, real ticks) on XAUUSD.duka for a Tester window, with the stored ticks and
    M1 bars of a readback range written at init and at deinit. New run ID per tag; an existing run is never reused."""
    rid = probe_run_id(args.tag)
    if (runner.RUNS / rid).exists():
        raise SystemExit(f"{rid} exists; earlier runs are kept - use a new tag")
    cfg = env.load_config()
    shutil.copy2(SRC_DIR / DUMP_EA, cfg.mt5_dir / "MQL5" / "Experts" / DUMP_EA)
    comp = compmod.compile_ea(cfg, DUMP_EA)
    if comp["errors"] != 0 or not comp["ex5_exists"]:
        raise SystemExit(f"compile failed: {comp['log'][-1500:]}")
    ms = lambda s: int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    lines = [f"ResearchRunTag={rid}", f"ReadbackFromMsc={ms(args.rb_from)}", f"ReadbackToMsc={ms(args.rb_to) - 1}"]
    text = ini.render(expert=pathlib.Path(DUMP_EA).stem + ".ex5", symbol=CUSTOM, period="M1",
                      from_date=args.date_from, to_date_inclusive=args.date_to, deposit=10000,
                      report=f"reports\\{rid}", set_lines=lines)
    res = runner.run(cfg, rid, text, pathlib.Path(DUMP_EA).stem + ".ex5", meta={"role": "duka_data_check_v2"})
    print(rid, res.status, res.seconds)


def agent_lines(rid: str) -> list:
    """The Tester agent's lines of this run only (the day's log holds earlier runs): from the agent start before
    the line naming the run to the agent stop after it; kept are the start shift, synchronization and tick lines."""
    out = []
    keys = ("start time changed", "ticks, ", "history synchronized", "ticks synchronized", "testing of",
            "history begins", "contains")
    for f in (runner.RUNS / rid / "logs").glob("Tester__Agent*"):
        lines = env.read_text(f, errors="replace").splitlines()
        tagged = [i for i, l in enumerate(lines) if f"ResearchRunTag={rid}" in l]
        if not tagged:
            continue
        i = tagged[-1]
        a = max([k for k in range(i) if "MetaTester 5 started" in lines[k]], default=0)
        b = min([k for k in range(i, len(lines)) if "MetaTester 5 stopped" in lines[k]], default=len(lines) - 1)
        out += [l.split("\t")[-1] for l in lines[a:b + 1] if any(w in l for w in keys)]
    return out


def cmd_probe_report(args) -> None:
    """What a probe run saw: spec (incl. formula), stored ticks and M1 bars at init and at end, the delivered
    OnTick sequence, and the Tester's own lines; compared with the source ticks of the window when given."""
    rid = probe_run_id(args.tag)
    d = runner.RUNS / rid
    read = lambda n: pd.read_csv(d / f"rl_{n}_{rid}.csv") if (d / f"rl_{n}_{rid}.csv").exists() else None
    spec = read("spec")
    out = {"run": rid, "tester_lines": [l for l in agent_lines(rid) if "Agent" not in l][-12:],
           "spec": dict(zip(spec["key"], spec["value"].astype(str))) if spec is not None else None}
    for n in ("readback", "readback_end"):
        t, r, m = read(n), read(n + "_rates"), read(n + "_meta")
        out[n] = {"meta": dict(zip(m["key"], m["value"].astype(str))) if m is not None else None,
                  "ticks": 0 if t is None else int(len(t)), "bars_m1": 0 if r is None else int(len(r))}
        if t is not None and len(t):
            out[n]["first_tick"] = _iso(int(t["time_msc"].min()))
            out[n]["last_tick"] = _iso(int(t["time_msc"].max()))
            out[n]["ticks_by_day"] = {str(k): int(v) for k, v in
                                      pd.to_datetime(t["time_msc"], unit="ms").dt.date.value_counts().sort_index().items()}
        if args.list and t is not None:
            out[n]["tick_rows"] = t.head(50).to_dict("records")
            out[n]["bar_rows"] = r.head(50).to_dict("records") if r is not None else []
    ticks = read("ticks")
    out["ontick"] = {"ticks": 0 if ticks is None else int(len(ticks))}
    if ticks is not None and len(ticks):
        out["ontick"].update(first=_iso(int(ticks["time_msc"].min())), last=_iso(int(ticks["time_msc"].max())))
    if args.span:                   # the imported span's feed ticks are the source of every comparison
        src_all, _ = span_ticks(args.span)
        for n in ("readback", "readback_end"):
            t, m = read(n), read(n + "_meta")
            if t is None or m is None:
                continue
            mm = dict(zip(m["key"], m["value"]))
            s = src_all[(src_all["time_msc"] >= int(mm["from_msc"])) & (src_all["time_msc"] <= int(mm["to_msc"]))]
            out[n]["vs_source"] = compare_ticks(s, t, f"{n}: stored ticks against the feed in the readback range")
        if args.window:
            frm, to = (int(dt.datetime.fromisoformat(x).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
                       for x in args.window)
            s = src_all[(src_all["time_msc"] >= frm) & (src_all["time_msc"] < to)]
            got = ticks if ticks is not None else s.iloc[:0]
            out["ontick_vs_source"] = compare_ticks(s, got, f"every OnTick tick against the feed in "
                                                            f"[{args.window[0]}, {args.window[1]})")
            out["ontick_outside_window"] = int(((got["time_msc"] < frm) | (got["time_msc"] >= to)).sum())
    evaluate.save(out, RESULTS2 / f"probe_{args.tag}.json")
    print(json.dumps({k: v for k, v in out.items() if k != "spec"}, indent=1, default=str)[:6000])


def cmd_verify_readback(args) -> None:
    """The stored ticks the Tester read back (CopyTicksRange at init) against the source, tick by tick."""
    src = source_ticks(args.month)
    rid = dump_run_id(CUSTOM, args.month) + "_rb"
    got = pd.read_csv(runner.RUNS / rid / f"rl_readback_{rid}.csv")
    meta = pd.read_csv(runner.RUNS / rid / f"rl_readback_meta_{rid}.csv")
    out = compare_ticks(src, got, f"stored ticks read back in the Tester ({rid})")
    out["readback_meta"] = dict(zip(meta["key"], meta["value"].astype(str)))
    out["flags"] = {str(k): int(v) for k, v in got["flags"].value_counts().items()}
    if len(got) == len(src):
        out["max_abs_time_diff_ms"] = int((got["time_msc"].to_numpy() - src["time_msc"].to_numpy()).__abs__().max())
    spec = pd.read_csv(runner.RUNS / rid / f"rl_spec_{rid}.csv")
    out["spec_in_tester"] = dict(zip(spec["key"], spec["value"].astype(str)))
    evaluate.save(out, RESULTS / f"readback_check_{args.month}.json")
    print(json.dumps({k: v for k, v in out.items() if k != "spec_in_tester"}, indent=1))


def cmd_verify_tester(args) -> None:
    src = source_ticks(args.month)
    rid = dump_run_id(CUSTOM, args.month)
    got = tester_ticks(rid)
    out = compare_ticks(src, got, f"Tester delivery ({rid})")
    spec = pd.read_csv(runner.RUNS / rid / f"rl_spec_{rid}.csv")
    out["spec_in_tester"] = dict(zip(spec["key"], spec["value"].astype(str)))
    evaluate.save(out, RESULTS / f"tester_check_{args.month}.json")
    print(json.dumps({k: v for k, v in out.items() if k != "spec_in_tester"}, indent=1))


# ------------------------------------------------------------------ March 2026: Dukascopy against Bybit
def cmd_compare(args) -> None:
    if args.month != "2026-03":
        raise SystemExit("the Bybit comparison is March 2026 only")
    duka, _ = load_month(args.month)
    by = tester_ticks(dump_run_id(BYBIT, args.month))     # Bybit server clock, as the Tester delivers it
    dm1, bm1 = dk.bars(duka, 60), dk.bars(by.rename(columns={}), 60)
    days = sorted(set(pd.to_datetime(dm1["time"], unit="s").dt.date))
    per_day = []
    for d in days:
        lo = int(dt.datetime(d.year, d.month, d.day, tzinfo=dk.UTC).timestamp())
        sub_d = dm1[(dm1["time"] >= lo) & (dm1["time"] < lo + 86400)]
        sub_b = bm1[(bm1["time"] >= lo - 6 * 3600) & (bm1["time"] < lo + 86400 + 6 * 3600)]
        r = dk.best_offset(sub_d, sub_b)
        per_day.append({"day": str(d), "best_hours": r["best_hours"],
                        "best_median_abs_close_diff": r["scores"].get(r["best_hours"], {}).get("median_abs_close_diff")})
    out = {"month": args.month, "offset_per_day": per_day}
    # after aligning by each day's evidenced offset (for comparison only; the stored data stay unchanged)
    off = {p["day"]: p["best_hours"] for p in per_day}
    shift = pd.to_datetime(bm1["time"], unit="s").dt.date.map(lambda d: off.get(str(d)))
    bm1a = bm1.assign(time=bm1["time"] - shift.fillna(0).astype(int) * 3600)
    j = dm1.merge(bm1a, on="time", suffixes=("_duka", "_bybit"))
    diff = j["close_duka"] - j["close_bybit"]
    out["aligned_minutes"] = int(len(j))
    out["close_diff_duka_minus_bybit"] = {k: round(float(v), 3) for k, v in
                                          zip(("p01", "median", "p99", "mean_abs"),
                                              [diff.quantile(0.01), diff.median(), diff.quantile(0.99), diff.abs().mean()])}
    out["spread"] = {"duka_median": round(float((duka["ask"] - duka["bid"]).median()), 3),
                     "bybit_median": round(float((by["ask"] - by["bid"]).median()), 3),
                     "duka_p99": round(float((duka["ask"] - duka["bid"]).quantile(0.99)), 3),
                     "bybit_p99": round(float((by["ask"] - by["bid"]).quantile(0.99)), 3)}
    out["ticks"] = {"duka": int(len(duka)), "bybit": int(len(by)),
                    "per_aligned_minute_median_duka": float(j["ticks_duka"].median()),
                    "per_aligned_minute_median_bybit": float(j["ticks_bybit"].median())}
    out["minutes_only_in_duka"] = int(len(set(dm1["time"]) - set(bm1a["time"])))
    out["minutes_only_in_bybit"] = int(len(set(bm1a["time"]) - set(dm1["time"])))
    hod = lambda t: pd.to_datetime(t, unit="s").dt.hour
    out["quote_hours_utc_duka"] = sorted(int(h) for h in hod(dm1["time"]).unique())
    out["quote_hours_bybit_server_clock"] = sorted(int(h) for h in hod(bm1["time"]).unique())
    out["daily_break"] = {"duka_utc": _breaks(duka["time_msc"]), "bybit_server": _breaks(by["time_msc"])}
    evaluate.save(out, RESULTS / f"compare_bybit_{args.month}.json")
    print(json.dumps({k: v for k, v in out.items() if k not in ("offset_per_day",)}, indent=1, default=str)[:5000])


def _breaks(time_msc: pd.Series) -> list:
    """Pauses of 50-80 minutes on weekdays: start and end time of day, per day (clock of the source)."""
    t = np.sort(time_msc.to_numpy())
    out = []
    for a, b in zip(t[:-1], t[1:]):
        if 50 * 60 * 1000 <= b - a <= 80 * 60 * 1000:
            sa, sb = (dt.datetime.fromtimestamp(x / 1000, dk.UTC) for x in (a, b))
            out.append({"day": str(sa.date()), "from": sa.strftime("%H:%M:%S"), "to": sb.strftime("%H:%M:%S")})
    return out


COMMANDS = {"download": cmd_download, "manifest": cmd_manifest, "quality": cmd_quality, "export": cmd_export,
            "import": cmd_import, "verify-import": cmd_verify_import, "tester-dump": cmd_tester_dump,
            "verify-tester": cmd_verify_tester, "compare": cmd_compare, "verify-readback": cmd_verify_readback,
            "custom-backup": cmd_custom_backup, "tester-probe": cmd_tester_probe, "probe-report": cmd_probe_report,
            "manual-check": cmd_manual_check, "export-sample": cmd_export_sample,
            "manual-import-file": cmd_manual_import_file, "gui-prepare": cmd_gui_prepare, "gui-finish": cmd_gui_finish}
SAMPLE_OK = ("verify-import", "tester-dump", "verify-tester", "verify-readback")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in COMMANDS:
        sp = sub.add_parser(name)
        if name == "tester-dump":
            sp.add_argument("symbol")
        if name in SAMPLE_OK:
            sp.add_argument("month", choices=sorted(MONTHS) + [SAMPLE])
        elif name not in ("import", "manual-check", "export-sample", "manual-import-file", "gui-prepare",
                          "gui-finish", "custom-backup", "tester-probe", "probe-report"):
            sp.add_argument("month", choices=sorted(MONTHS))
        if name == "download":
            sp.add_argument("--pause", type=float, default=5.0, help="seconds between requests")
            sp.add_argument("--wait", type=int, default=900, help="seconds after a 429 without Retry-After")
            sp.add_argument("--max-hours", type=float, default=12.0, help="stop (resumable) after this long")
            sp.add_argument("--max-failures", type=int, default=4, help="network failures in a row before stopping")
        if name == "tester-dump":
            sp.add_argument("--readback", action="store_true", help="also write the stored ticks of the period")
        if name in ("tester-probe", "probe-report"):
            sp.add_argument("tag")
        if name == "tester-probe":
            sp.add_argument("date_from", help="Tester FromDate YYYY.MM.DD")
            sp.add_argument("date_to", help="Tester last day YYYY.MM.DD (inclusive)")
            sp.add_argument("rb_from", help="readback start, ISO UTC")
            sp.add_argument("rb_to", help="readback end (exclusive), ISO UTC")
        if name == "probe-report":
            sp.add_argument("--span", choices=sorted(SPANS), help="the imported span: source of the comparisons")
            sp.add_argument("--window", nargs=2, help="Tester window [from, to) ISO UTC: every OnTick tick against the span")
            sp.add_argument("--list", action="store_true", help="include the first 50 stored ticks and bars")
        if name == "manual-import-file":
            sp.add_argument("--span", choices=["sample", "span0102"], default="sample")
        if name == "quality":
            sp.add_argument("--partial", action="store_true", help="a labelled check of an incomplete month")
        if name == "import":
            sp.add_argument("--timeout", type=int, default=1800)
            sp.add_argument("--sample", action="store_true", help="import only the one-hour sample file")
    args = ap.parse_args()
    COMMANDS[args.cmd](args)


if __name__ == "__main__":
    main()
