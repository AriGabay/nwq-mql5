"""Append-only experiment log (R20): JSONL source of truth plus a derived CSV view."""
import csv
import datetime as dt
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
LOG = REPO / "results" / "experiment_log.jsonl"
FIELDS = ["id", "timestamp", "purpose", "role", "expert", "period", "from", "to", "deposit", "status",
          "net_profit", "trades", "equity_dd_pct", "notes"]


def append(entry: dict, log: pathlib.Path = None) -> None:
    """log defaults to the module's LOG at call time, so a study CLI can point every append at its own log."""
    log = log or LOG
    entry = dict(entry)
    entry.setdefault("timestamp", dt.datetime.now().isoformat(timespec="seconds"))
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as f:
        f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
    write_csv(log)


def read(log: pathlib.Path = None) -> list:
    log = log or LOG
    if not log.exists():
        return []
    return [json.loads(l) for l in log.read_text().splitlines() if l.strip()]


def write_csv(log: pathlib.Path = None) -> None:
    log = log or LOG
    rows = read(log)
    with open(log.with_suffix(".csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
