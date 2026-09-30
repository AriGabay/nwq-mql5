"""Pipeline entry point: python research/cli.py <step>

Steps: wfo (U6 folds + final selection), robustness (U8), deliver (U9).
Every step reads research/preregistration.json and refuses to run when it has uncommitted changes.
"""
import argparse
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from mt5r import curate, env, explog, pipeline, reports, wfo  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
P = pipeline.PREREG
AXES = P["grid"]
PARAMS = list(AXES)
DEFAULTS = {"PivL": 3, "PivR": 3, "SweepToSetupBars": 12, "VolumeMultiplier": 2.0, "ConfirmationBars": 6}


def prereg_committed() -> None:
    r = subprocess.run(["git", "status", "--porcelain", "research/preregistration.json"], cwd=REPO,
                       capture_output=True, text=True)
    if r.stdout.strip():
        raise SystemExit("preregistration.json has uncommitted changes; refusing to run")


def grid_for_window(cfg, tag: str, start: str, end: str):
    """Four complete optimizations (one per VolumeMultiplier) merged into the 576-row grid."""
    ranges = {k: tuple(v) for k, v in P["grid_ranges"].items()}
    parts, frames = [], []
    for vm in AXES["VolumeMultiplier"]:
        run_id = f"{tag}_vm{str(vm).replace('.', '')}"
        res, df = pipeline.run_optimization(cfg, run_id, "research", P["period"], start, end,
                                            {"VolumeMultiplier": vm}, ranges, role="train", purpose=f"{tag} grid")
        if df is None:
            raise SystemExit(f"optimization {run_id} failed")
        df["VolumeMultiplier"] = vm
        parts.append(df)
        curate.curate(run_id, "wfo")
    grid = wfo.merge_grids([p[PARAMS + ["profit", "trades", "eq_dd_pct", "recovery_factor", "custom", "sharpe"]]
                            for p in parts], AXES)
    rf = grid["recovery_factor"]
    grid["eq_dd_money"] = (grid["profit"] / rf).abs().where(rf != 0, 1.0)
    return grid


def baseline_trades(cfg, tag: str, start: str, end: str) -> int:
    res, rep = pipeline.run_single(cfg, f"{tag}_baseline_train", "research", P["period"], start, end,
                                   role="train_baseline_count", purpose=f"{tag} baseline trade count (T only)")
    return reports.summary(rep)["trades"]


def select_on(cfg, tag: str, start: str, end: str) -> dict:
    n_base = baseline_trades(cfg, tag, start, end)
    floor = wfo.trade_floor(n_base)
    grid = grid_for_window(cfg, tag, start, end)
    sel = wfo.select(grid, PARAMS, DEFAULTS, floor, P["selection"]["max_equity_dd_pct"])
    out = REPO / "results" / "wfo" / f"{tag}_selection"
    out.mkdir(parents=True, exist_ok=True)
    sel["scored"].to_csv(out / "scored_grid.csv", index=False)
    info = {"tag": tag, "train": [start, end], "baseline_trades": n_base, "trade_floor": floor,
            "status": sel["status"], "params": sel["params"],
            "eligible_passes": int(sel["scored"]["eligible"].sum())}
    (out / "selection.json").write_text(json.dumps(info, indent=1))
    explog.append({"id": f"{tag}_selection", "purpose": "R16 selection", "role": "selection", "status": sel["status"],
                   "notes": json.dumps(info)})
    return info


def cmd_wfo(args) -> None:
    prereg_committed()
    cfg = env.load_config()
    folds_out = REPO / "results" / "wfo" / "folds.json"
    done = json.loads(folds_out.read_text()) if folds_out.exists() else []
    deposits = {"procedure": P["deposit"], "baseline": P["deposit"]}
    for rec in done:
        deposits = rec["next_deposits"]
    for fold in P["folds"][len(done):]:
        k = fold["fold"]
        info = select_on(cfg, f"f{k}", *fold["train"])
        rec = {"fold": k, "selection": info, "test": fold["test"], "deposits": dict(deposits), "oos": {}}
        for who, params in (("procedure", info["params"]), ("baseline", {})):
            run_id = f"f{k}_oos_{who}"
            res, rep = pipeline.run_single(cfg, run_id, "research", P["period"], *fold["test"],
                                           deposit=deposits[who], overrides=params, role=f"oos_{who}",
                                           purpose=f"fold {k} OOS {who}")
            s = reports.summary(rep)
            final = deposits[who] + s["net_profit"]
            rec["oos"][who] = {**s, "final_balance": round(final, 2), "run_id": run_id}
            curate.curate(run_id, "wfo")
        deposits = {w: rec["oos"][w]["final_balance"] for w in rec["oos"]}
        rec["next_deposits"] = deposits
        done.append(rec)
        folds_out.write_text(json.dumps(done, indent=1))
        print(f"fold {k}: {info['status']} {info['params']} | OOS procedure {rec['oos']['procedure']['net_profit']:.2f}"
              f" baseline {rec['oos']['baseline']['net_profit']:.2f}", flush=True)
    final = REPO / "results" / "final_selection" / "selection.json"
    if not final.exists():
        info = select_on(cfg, "final", *P["final_train"])
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_text(json.dumps(info, indent=1))
        print(f"final candidate: {info['status']} {info['params']}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("wfo")
    args = ap.parse_args()
    {"wfo": cmd_wfo}[args.cmd](args)


if __name__ == "__main__":
    main()
