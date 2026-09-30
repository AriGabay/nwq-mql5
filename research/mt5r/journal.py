"""Tester journal facts (R11): real-tick coverage, generated-tick substitution, warm-up, EA init line."""
import pathlib
import re

REDACT_ACCT = re.compile(r"(?<![\d.])\d{7,8}(?![\d.])")
REDACT_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def read(path) -> str:
    data = pathlib.Path(path).read_bytes()
    return data.decode("utf-16", "replace") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else data.decode("utf-8", "replace")


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
    m = re.search(r"rejected by volume: (\d+)", text)
    if m:
        out["funnel"]["rejected_by_volume"] = int(m.group(1))
    m = re.search(r"FVG candidates seen \(bar B in window\): (\d+)", text)
    if m:
        out["funnel"]["fvg_candidates"] = int(m.group(1))
    m = re.search(r"orders sent: (\d+)", text)
    if m:
        out["funnel"]["orders_sent"] = int(m.group(1))
    return out


def run_facts(run_dir) -> dict:
    """Facts from the tester (manager) log of an archived run directory."""
    logs = sorted(pathlib.Path(run_dir, "logs").glob("Tester__logs__*.log"))
    text = "\n".join(read(p) for p in logs)
    # keep only the last test in the shared daily log
    idx = text.rfind("testing of Experts")
    return facts(text[idx:] if idx >= 0 else text)
