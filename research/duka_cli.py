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
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

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
def fetch(u: str, tries: int = 4) -> tuple:
    """(http status, body). 404 is an answer (no file for that hour); 429 raises RateLimited at once; network
    errors are retried a few times, then raised."""
    err = None
    for k in range(tries):
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
        time.sleep(5 * (k + 1))
    raise NetworkError(f"{u}: {err}")


def status_path(month: str) -> pathlib.Path:
    return RAW / SYMBOL / f"download_{month}.json"


def load_status(month: str) -> dict:
    p = status_path(month)
    st = json.loads(p.read_text()) if p.exists() else {}
    for k, v in (("hours", {}), ("rate_limits", []), ("runs", [])):
        st.setdefault(k, v)
    return st


def save_status(month: str, st: dict) -> None:
    p = status_path(month)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    tmp.replace(p)


def now_iso() -> str:
    return dt.datetime.now(dk.UTC).isoformat(timespec="seconds")


def cmd_download(args) -> None:
    """Every hour of the month once: a stored file (possibly an empty 200 answer), a 404, or - after a network
    failure - unresolved for a later run. A 429 waits Retry-After (or args.wait seconds) and retries the same hour;
    each 429 is logged with its time, so the limit is documented from what was observed."""
    y, m = check_month(args.month)
    st = load_status(args.month)
    t_end = time.time() + args.max_hours * 3600
    got = 0
    for h in dk.month_hours(y, m):
        key = h.isoformat()
        p = raw_path(h)
        if p.exists():                                      # never overwritten
            st["hours"].setdefault(key, {"status": "file" if p.stat().st_size else "empty_response"})
            continue
        if st["hours"].get(key, {}).get("status") == "http_404":
            continue
        while True:
            if time.time() > t_end:
                st["runs"].append({"at": now_iso(), "downloaded": got, "stopped": "max_hours"})
                save_status(args.month, st)
                raise SystemExit(f"{args.month}: stopped after --max-hours; resume later")
            try:
                status, body = fetch(dk.url(SYMBOL, h))
            except RateLimited as e:
                wait = e.retry_after if e.retry_after is not None else args.wait
                st["rate_limits"].append({"at": now_iso(), "hour": key, "retry_after": e.retry_after, "waited_s": wait})
                save_status(args.month, st)
                time.sleep(wait)
                continue
            except NetworkError as e:
                st["hours"][key] = {"status": "unresolved", "error": str(e)[-200:], "at": now_iso()}
                break
            if status == 404:
                st["hours"][key] = {"status": "http_404", "at": now_iso()}
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(body)
                st["hours"][key] = {"status": "file" if body else "empty_response", "bytes": len(body), "at": now_iso()}
                got += 1
            break
        save_status(args.month, st)
        time.sleep(args.pause)
    st["runs"].append({"at": now_iso(), "downloaded": got, "stopped": None})
    save_status(args.month, st)
    left = sum(1 for v in st["hours"].values() if v["status"] == "unresolved")
    print(f"{args.month}: downloaded {got} this run; unresolved hours {left}; 429 events so far {len(st['rate_limits'])}")


def load_month(key: str):
    """Decoded ticks of a month in hour order, plus one manifest row per hour with its download status."""
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


def closed_hours(files: list, ticks: pd.DataFrame) -> set:
    """Hours without ticks that lie wholly inside an observed market pause between real ticks (a weekend, holiday
    or daily-break gap from dk.gaps). Only those count as a verified close; an hour never downloaded never does."""
    out = set()
    for g in dk.gaps(ticks, min_seconds=3600, holidays=HOLIDAYS):
        if g["kind"] == "unexplained":
            continue
        a, b = dt.datetime.fromisoformat(g["start"]), dt.datetime.fromisoformat(g["end"])
        for f in files:
            h = dt.datetime.fromisoformat(f["hour"])
            if a <= h and h + dt.timedelta(hours=1) <= b:
                out.add(f["hour"])
    return out


def cmd_manifest(args) -> None:
    ticks, files = load_month(args.month)
    closed = closed_hours(files, ticks)
    for f in files:
        if f["status"] in ("empty_response", "http_404"):
            f["verified_market_closed"] = f["hour"] in closed
    present = [f for f in files if f["file"]]
    counts = pd.Series([f["status"] for f in files]).value_counts().to_dict()
    pending = counts.get("not_downloaded", 0) + counts.get("unresolved", 0)
    st = load_status(args.month)
    man = {"source": "Dukascopy historical data feed (free), " + dk.BASE + "/XAUUSD/<YYYY>/<MM-1>/<DD>/<HH>h_ticks.bi5",
           "format": "LZMA; 20-byte big-endian records: uint32 ms since the UTC hour, uint32 ask, uint32 bid, "
                     "float32 ask volume, float32 bid volume; price = integer / 1000",
           "symbol": SYMBOL, "month": args.month, "time_zone": "UTC (hour in the path, ms offset in the record)",
           "raw_location": "outside git (NWQ_DUKA_DIR, default D:/data/dukascopy)",
           "complete": pending == 0, "hours_in_month": len(files), "hours_by_status": counts, "hours_pending": pending,
           "empty_hours_in_verified_closed_window": sum(1 for f in files if f.get("verified_market_closed")),
           "empty_hours_outside_closed_window": sum(1 for f in files if f["status"] in ("empty_response", "http_404")
                                                    and not f.get("verified_market_closed")),
           "files": len(present), "bytes": sum(f["bytes"] for f in present), "ticks": int(len(ticks)),
           "first_tick_utc": _iso(ticks["time_msc"].min()) if len(ticks) else None,
           "last_tick_utc": _iso(ticks["time_msc"].max()) if len(ticks) else None,
           "rate_limit_events": len(st["rate_limits"]), "rate_limits_observed": st["rate_limits"],
           "sha256_of_file_list": sha256("".join(f"{f['file']}:{f['sha256']}\n" for f in present).encode()),
           "per_hour": files}
    evaluate.save(man, RESULTS / f"manifest_{args.month}.json")
    print(json.dumps({k: v for k, v in man.items() if k not in ("per_hour", "rate_limits_observed")}, indent=1))


def require_complete(month: str, allow_partial: bool) -> None:
    man = RESULTS / f"manifest_{month}.json"
    if not man.exists():
        raise SystemExit(f"run manifest {month} first")
    m = json.loads(man.read_text())
    if not m["complete"] and not allow_partial:
        raise SystemExit(f"{month}: {m['hours_pending']} hours not downloaded - the month is partial "
                         "(--partial only for a labelled partial check)")


# ------------------------------------------------------------------ quality
def derived(name: str) -> pathlib.Path:
    p = RAW / "derived" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def cmd_quality(args) -> None:
    require_complete(args.month, args.partial)
    ticks, files = load_month(args.month)
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
    out = {"month": args.month, "partial_sample": bool(args.partial), "quality": q,
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


def bin_name(month: str) -> str:
    return f"duka_XAUUSD_{month}.bin"


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
    require_complete(args.month, False)
    ticks, _ = load_month(args.month)
    q = dk.quality(ticks)
    if q["out_of_order"]:
        raise SystemExit(f"{args.month}: {q['out_of_order']} ticks out of time order - not exported; decide first")
    cfg = env.load_config()
    frm, to = month_range_msc(args.month)
    dst = mt5_files(cfg) / bin_name(args.month)
    write_ticks_bin(dst, ticks, frm, to)
    print(f"{dst}: {len(ticks)} ticks, {dst.stat().st_size} bytes, sha256 {sha256(dst.read_bytes())}")


def startup_ini() -> str:
    """Offline start: an unknown server and a closed-port proxy, so no account can connect; trading off; the import
    script at startup; the terminal shuts down after it."""
    return "\r\n".join([
        "[Common]", "Server=nwq-offline-no-server", "Login=0", "ProxyEnable=1", "ProxyType=0",
        "ProxyAddress=127.0.0.1:9", "NewsEnable=0",
        "[Experts]", "AllowLiveTrading=0", "AllowDllImport=0", "Enabled=0",
        "[StartUp]", f"Script={pathlib.Path(IMPORT_SCRIPT).stem}", "ShutdownTerminal=1", ""])


TRADE_CALLS = ("OrderSend", "OrderSendAsync", "PositionOpen", "PositionClose", "PositionModify", "CTrade",
               "Trade.mqh", "OrderCalcProfit")


def assert_no_trading_calls(src: pathlib.Path) -> None:
    text = src.read_text(encoding="utf-8")
    hits = [c for c in TRADE_CALLS if c in text]
    if hits:
        raise SystemExit(f"{src.name} contains trading calls {hits}; refusing")


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
    for m in MONTHS:
        if not (mt5_files(cfg) / bin_name(m)).exists():
            raise SystemExit(f"{bin_name(m)} missing in MQL5/Files: run export {m} first")
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
    env.write_utf16(cfg.mt5_dir / ini_rel, startup_ini())
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
    for f in cfg.mt5_dir.glob("logs/*.log"):
        if f.stat().st_mtime >= t0 - 5:
            shutil.copy2(f, keep / f.name)
    journal = "".join(env.read_text(f, errors="replace") for f in keep.glob("*.log"))
    connected = [l for l in journal.splitlines() if any(w in l.lower() for w in ("authorized on", "connected to"))]
    log_text = log_file.read_text(encoding="latin-1") if log_file.exists() else ""
    if log_file.exists():
        shutil.copy2(log_file, keep / log_file.name)
    out = {"status": status, "seconds": round(time.time() - t0, 1), "common_ini_changed_by_terminal": restored,
           "common_ini_restored": True, "account_connection_lines": connected, "script_log": log_text.splitlines(),
           "journal_kept_in": keep.as_posix()}
    evaluate.save(out, RESULTS / "import_run.json")
    print(json.dumps(out, indent=1))
    if connected or "result: ok" not in log_text:
        raise SystemExit("import not clean - see import_run.json")


def cmd_verify_import(args) -> None:
    cfg = env.load_config()
    src, _ = load_month(args.month)
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
    check_month(args.month)
    if args.symbol not in (CUSTOM, BYBIT):
        raise SystemExit(f"symbol must be {CUSTOM} or {BYBIT}")
    if args.symbol == BYBIT and args.month != "2026-03":
        raise SystemExit("the Bybit comparison is March 2026 only")
    cfg = env.load_config()
    shutil.copy2(SRC_DIR / DUMP_EA, cfg.mt5_dir / "MQL5" / "Experts" / DUMP_EA)
    comp = compmod.compile_ea(cfg, DUMP_EA)
    if comp["errors"] != 0 or not comp["ex5_exists"]:
        raise SystemExit(f"compile failed: {comp['log'][-1500:]}")
    hs = dk.month_hours(*check_month(args.month))
    rid = dump_run_id(args.symbol, args.month)
    text = ini.render(expert=pathlib.Path(DUMP_EA).stem + ".ex5", symbol=args.symbol, period="M1",
                      from_date=hs[0].strftime("%Y.%m.%d"), to_date_inclusive=hs[-1].strftime("%Y.%m.%d"),
                      deposit=10000, report=f"reports\\{rid}", set_lines=[f"ResearchRunTag={rid}"])
    res = runner.run(cfg, rid, text, pathlib.Path(DUMP_EA).stem + ".ex5", meta={"role": "duka_data_check"})
    print(rid, res.status, res.seconds)


def tester_ticks(rid: str) -> pd.DataFrame:
    return pd.read_csv(runner.RUNS / rid / f"rl_ticks_{rid}.csv")


def cmd_verify_tester(args) -> None:
    src, _ = load_month(args.month)
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
            "verify-tester": cmd_verify_tester, "compare": cmd_compare}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in COMMANDS:
        sp = sub.add_parser(name)
        if name == "tester-dump":
            sp.add_argument("symbol")
        if name != "import":
            sp.add_argument("month", choices=sorted(MONTHS))
        if name == "download":
            sp.add_argument("--pause", type=float, default=5.0, help="seconds between requests")
            sp.add_argument("--wait", type=int, default=900, help="seconds after a 429 without Retry-After")
            sp.add_argument("--max-hours", type=float, default=12.0, help="stop (resumable) after this long")
        if name == "quality":
            sp.add_argument("--partial", action="store_true", help="a labelled check of an incomplete month")
        if name == "import":
            sp.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args()
    COMMANDS[args.cmd](args)


if __name__ == "__main__":
    main()
