"""Tester journal facts (R26): real-tick coverage, generated-tick substitution, warm-up, EA init and Funnel lines."""
import pathlib
import re

from .textio import read_text

REDACT_ACCT = re.compile(r"(?<![\d.])\d{7,8}(?![\d.])")
FUNNEL_RE = re.compile(r"Funnel:((?:[ \t]+\w+=-?\d+)+)")   # EA OnDeinit lines (interface contract, AMENDMENT B)
FUNNEL_KV = re.compile(r"(\w+)=(-?\d+)")
REDACT_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def read(path) -> str:
    return read_text(path, errors="replace", utf16_errors="replace")


def redact(line: str) -> str:
    """Mask account numbers and IP addresses before a line is quoted into results/."""
    return REDACT_IP.sub("<ip>", REDACT_ACCT.sub("<acct>", line))


def facts(text: str) -> dict:
    out = {"discarded_days": 0, "discarded_minutes": 0, "total_minute_bars": None, "real_ticks_begin": None,
           "warmup_bars": None, "stops_level_pts": None, "tick": None, "final_balance": None,
           "every_tick_generation_used": False, "funnel": {}}
    m = re.search(r"real ticks discarded for (\d+) minutes of (\d+) total minute bars", text)
    if m:
        out["discarded_minutes"], out["total_minute_bars"] = int(m.group(1)), int(m.group(2))
        out["every_tick_generation_used"] = "every tick generation used" in text
    m = re.search(r"real ticks discarded for (\d+) whole days", text)
    if m:
        out["discarded_days"] = int(m.group(1))
    m = re.search(r"real ticks begin from (\S+ \S+)", text)
    if m:
        out["real_ticks_begin"] = m.group(1)
    m = re.search(r"Warm-up bars: (\d+)", text)
    if m:
        out["warmup_bars"] = int(m.group(1))
    m = re.search(r"tick ([\d.]+), stops level (\d+) pts", text)
    if m:
        out["tick"], out["stops_level_pts"] = float(m.group(1)), int(m.group(2))
    m = re.search(r"final balance ([\d.]+)", text)
    if m:
        out["final_balance"] = float(m.group(1))
    # The EA prints the funnel over several "Funnel:" lines (MT5's journal truncates long lines): merge them all,
    # later keys overriding earlier ones. run_facts() passes only the last test's text.
    for line in FUNNEL_RE.findall(text):
        out["funnel"].update({k: int(v) for k, v in FUNNEL_KV.findall(line)})
    return out


def run_facts(run_dir) -> dict:
    """Facts from the tester (manager) log of an archived run directory."""
    logs = sorted(pathlib.Path(run_dir, "logs").glob("Tester__logs__*.log"))
    text = "\n".join(read(p) for p in logs)
    # keep only the last test in the shared daily log
    idx = text.rfind("testing of Experts")
    return facts(text[idx:] if idx >= 0 else text)
