"""High-level run helpers shared by the CLI steps (U5, U6, U8, U9)."""
import json
import pathlib

from . import env as envmod
from . import explog, ini, reports, runner, setfile

REPO = pathlib.Path(__file__).resolve().parents[2]
PREREG = json.loads((REPO / "research" / "preregistration.json").read_text())

BUILDS = {
    # kind: (ex5 name, source for input specs, research inputs?)
    "v103": ("new_test_v103.ex5", REPO / "original" / "new_test_v1.03.mq5", False),
    "v104": ("new_test.ex5", REPO / "mql5" / "Experts" / "new_test.mq5", False),
    "research": ("new_test_research.ex5", REPO / "mql5" / "Experts" / "new_test.mq5", True),
}


def specs(kind: str) -> list:
    ex5, src, research = BUILDS[kind]
    return setfile.parse_inputs(src.read_text(), research=research)


def base_values(kind: str, tf: int) -> dict:
    """R8 code defaults + R10 display settings (+ SignalTF for v1.04 builds)."""
    vals = dict(PREREG["display_inputs"])
    vals.update(PREREG["risk_inputs"])
    if kind != "v103":
        vals["SignalTF"] = tf
    return vals


def run_single(cfg, run_id: str, kind: str, period: str, start: str, end: str, deposit: float = 10000.0,
               overrides: dict = None, execution_mode: int = 0, role: str = "", purpose: str = "",
               timeout: int = 7200):
    """One single test; returns (RunResult, parsed report or None)."""
    tf = ini.PERIODS[period] if period != "H1" else 16385
    vals = base_values(kind, tf)
    vals.update(overrides or {})
    if kind == "research":
        vals["ResearchRunTag"] = run_id
    lines = setfile.render_lines(specs(kind), vals)
    ex5 = BUILDS[kind][0]
    text = ini.render(expert=ex5, symbol=PREREG["symbol"], period=period, from_date=start, to_date_inclusive=end,
                      deposit=deposit, report=f"reports\\{run_id}", set_lines=lines, execution_mode=execution_mode)
    res = runner.run(cfg, run_id, text, ex5, timeout=timeout, meta={"kind": kind, "role": role, "values": vals})
    rep = reports.parse_html(res.report) if res.report else None
    entry = {"id": run_id, "purpose": purpose, "role": role, "expert": ex5, "period": period, "from": start,
             "to": end, "deposit": deposit, "status": res.status, "seconds": res.seconds,
             "overrides": overrides or {}, "execution_mode": execution_mode}
    if rep:
        s = reports.summary(rep)
        entry.update({"net_profit": s["net_profit"], "trades": s["trades"], "equity_dd_pct": s["equity_dd_pct"],
                      "build": rep["header"].get("Build"), "history_quality": rep["header"].get("History Quality")})
    explog.append(entry)
    return res, rep


def check_inputs_loaded(rep: dict, expected: dict) -> list:
    """Mismatches between the report's input block and the intended values (R9/R29)."""
    bad = []
    for k, v in expected.items():
        got = rep["inputs"].get(k)
        if got is None or not _same(got, v):
            bad.append((k, v, got))
    return bad


def _same(a, b) -> bool:
    a, b = str(a).strip().lower(), str(b).strip().lower()
    if a == b:
        return True
    try:
        return abs(float(a) - float(b)) < 1e-9
    except ValueError:
        return False


def run_optimization(cfg, run_id: str, kind: str, period: str, start: str, end: str, fixed: dict,
                     ranges: dict, deposit: float = 10000.0, role: str = "", purpose: str = "",
                     timeout: int = 14400):
    """Complete (Optimization=1) run; returns (RunResult, passes DataFrame or None)."""
    tf = ini.PERIODS[period]
    vals = base_values(kind, tf)
    vals.update(fixed)
    if kind == "research":
        vals["ResearchRunTag"] = run_id
    lines = setfile.render_lines(specs(kind), vals, ranges)
    ex5 = BUILDS[kind][0]
    text = ini.render(expert=ex5, symbol=PREREG["symbol"], period=period, from_date=start, to_date_inclusive=end,
                      deposit=deposit, report=f"reports\\{run_id}", set_lines=lines, optimization=1)
    res = runner.run(cfg, run_id, text, ex5, timeout=timeout,
                     meta={"kind": kind, "role": role, "values": vals, "ranges": ranges})
    df = reports.parse_opt_xml(res.report) if res.report and res.report.suffix == ".xml" else None
    explog.append({"id": run_id, "purpose": purpose, "role": role, "expert": ex5, "period": period, "from": start,
                   "to": end, "deposit": deposit, "status": res.status if df is not None else "failed",
                   "seconds": res.seconds, "fixed": fixed, "ranges": ranges,
                   "passes": None if df is None else len(df)})
    return res, df
