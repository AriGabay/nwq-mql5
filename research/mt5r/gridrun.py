"""Split-range optimization of a non-uniform grid, pass verification against the tester's own output, and the
merge into one table with every grid tuple exactly once (plan 2026-10-04-1851, KTD3, KTD4, U3).

The tester's output is the evidence: the frames file (one P-row per completed pass, with the full input string),
the optimization XML, and this run's section of the shared daily manager log. A pass served from the tester cache
writes no frame and no 'new records saved to cache' line, so a cached or partial run fails here.
"""
import json
import pathlib
import re

import pandas as pd

from . import journal, reports, wfo

METRICS = ["profit", "trades", "eq_dd_pct", "recovery_factor", "custom", "sharpe"]
START_MARK = "complete optimization started"


def run_log_section(run_dir: pathlib.Path) -> str:
    """This run's section of the shared daily tester (manager) log: from the last optimization start onward."""
    logs = sorted(pathlib.Path(run_dir, "logs").glob("Tester__logs__*.log"))
    text = "\n".join(journal.read(p) for p in logs)
    idx = text.rfind(START_MARK)
    return text[idx:] if idx >= 0 else ""


def _int_params(row: dict, axes: list) -> dict:
    out = {}
    for a in axes:
        v = row.get(a)
        try:
            out[a] = int(float(v))
        except (TypeError, ValueError):
            out[a] = None
    return out


def verify_optimization(run_dir: pathlib.Path, run_id: str, expected: int, grid: dict, fixed: dict) -> dict:
    """Completion record of one optimization run; status 'ok' only when every source agrees on `expected` passes,
    every pass's inputs lie inside the grid (fixed inputs equal their fixed value) and XML and frames agree."""
    run_dir = pathlib.Path(run_dir)
    axes = list(grid)
    problems = []
    man_path = run_dir / "manifest.json"
    man = json.loads(man_path.read_text()) if man_path.exists() else {}
    frames_path, xml_path = run_dir / f"rl_frames_{run_id}.csv", run_dir / f"{run_id}.xml"
    passes = reports.read_frames(frames_path)[0] if frames_path.exists() else pd.DataFrame()
    xml = reports.parse_opt_xml(xml_path) if xml_path.exists() else pd.DataFrame()
    if xml.empty:
        problems.append("no optimization XML or no XML rows")
    log = run_log_section(run_dir)
    m_total = re.findall(r"total passes (\d+)", log)
    m_new = re.findall(r"(\d+) new records saved to cache", log)
    total = int(m_total[-1]) if m_total else None
    new_records = int(m_new[-1]) if m_new else 0
    completed = int(len(passes))
    if total is None:
        problems.append("the manager log has no 'total passes' line for this run")
    if new_records != expected:
        problems.append(f"{new_records} new cache records, expected {expected} (cached or partial run)")
    for name, n in (("frames", completed), ("XML", len(xml)), ("log total", total)):
        if n != expected:
            problems.append(f"{name} passes = {n}, expected {expected}")
    tuples = []
    if completed:
        if "pass" in xml.columns:
            xml_by_pass = {int(r["pass"]): r for r in xml.to_dict("records")}
        else:
            xml_by_pass = {}
        for row in passes.to_dict("records"):
            p = _int_params(row, axes)
            for a in axes:
                if p[a] is None or p[a] not in grid[a]:
                    problems.append(f"pass {row['pass']}: {a}={row.get(a)} is outside the grid")
            for a, v in fixed.items():
                if p.get(a) != v:
                    problems.append(f"pass {row['pass']}: fixed {a}={row.get(a)}, expected {v}")
            x = xml_by_pass.get(int(row["pass"]))
            if x is None:
                problems.append(f"pass {row['pass']} missing from the XML")
            else:
                for a in axes:
                    if a in x and p[a] is not None and int(float(x[a])) != p[a]:
                        problems.append(f"pass {row['pass']}: XML {a}={x[a]} but frames {a}={p[a]}")
            tuples.append(tuple(p[a] for a in axes))
    return {"run_id": run_id, "status": "ok" if not problems else "failed", "expected": expected,
            "completed": completed, "failed": max(expected - completed, 0),
            "cached": max(expected - new_records, 0) if total is not None else expected,
            "log_total_passes": total, "new_cache_records": new_records, "xml_rows": int(len(xml)),
            "seconds": man.get("seconds"), "exit_code": man.get("exit_code"), "problems": problems[:50],
            "tuples": tuples}


def run_table(run_dir: pathlib.Path, run_id: str, grid: dict, fixed: dict) -> pd.DataFrame:
    """One run's passes: grid inputs from the frames file (fixed inputs filled from their fixed value) joined to the
    XML metrics by pass number."""
    axes = list(grid)
    passes = reports.read_frames(pathlib.Path(run_dir) / f"rl_frames_{run_id}.csv")[0]
    xml = reports.parse_opt_xml(pathlib.Path(run_dir) / f"{run_id}.xml")
    rows = []
    xml_by_pass = {int(r["pass"]): r for r in xml.to_dict("records")}
    for row in passes.to_dict("records"):
        p = _int_params(row, axes)
        p.update(fixed)
        x = xml_by_pass[int(row["pass"])]
        rows.append({**p, **{m: x[m] for m in METRICS}, "run_id": run_id, "pass": int(row["pass"])})
    return pd.DataFrame(rows)


def merge(tables: list, grid: dict) -> pd.DataFrame:
    """The merged grid; wfo.merge_grids raises on a missing, extra or duplicated tuple (AE1)."""
    df = wfo.merge_grids(tables, grid)
    rf = df["recovery_factor"]
    df["eq_dd_money"] = (df["profit"] / rf).abs().where(rf != 0, 1.0)
    return df
