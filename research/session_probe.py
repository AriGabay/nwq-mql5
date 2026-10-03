"""Diagnosis of the SL/TP levels reached only in the first M1 bar after a quote gap (gate review, 2026-10-03).

The conformance checker found 38 pilot positions (29 in A, 9 in B) whose SL or TP level was reached by the Bid bars
only inside the first M1 bar after a quote gap (daily break, weekend), while the tester closed them later. This
script gathers tester evidence with a probe EA (research/mql5/session_probe.mq5) that runs only in the isolated
tester and never uses the strategy's rules:
  * the symbol's quote and trade sessions as the tester defines them;
  * every Bid/Ask tick from the last bar before each gap to 3 minutes after it, and around each actual exit;
  * a 0.01-lot Market order on the first tick after every quote gap and on the first tick of the next minute;
  * a replica of each case position (side, SL, TP; 0.01 lot) opened in the last bar before its gap, to see when
    and why the tester closes it.

python research/session_probe.py run       # one tester run (live terminal must be closed)
python research/session_probe.py analyze   # -> results/pilot/session_probe/
"""
import json
import pathlib
import shutil
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from mt5r import compile as compmod, conformance_m1 as cm, env, explog, ini, runner  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
RUN = json.loads((REPO / "research" / "run_constants.json").read_text())
SRC = REPO / "research" / "mql5" / "session_probe.mq5"
RUN_ID = "session_probe"
OUT = REPO / "results" / "pilot" / "session_probe"
PAD_S = 180
PILOT_RUNS = ("pilot_a", "pilot_b")


def cases(run_ids=PILOT_RUNS) -> pd.DataFrame:
    """The checker's level_in_session_open_bar positions, rebuilt with its own bar logic (conformance_m1._Bars).
    case_id = (index of the run + 1) x 1e6 + setup id."""
    rows = []
    for k, run_id in enumerate(run_ids):
        base, v = (k + 1) * 1_000_000, run_id.split("_")[-1]
        run = cm.read_run(runner.RUNS / run_id, run_id)
        B = cm._Bars(run["bars_m1"], 60)
        s = run["setups"]
        for r in s[s["reason"] == "filled"].to_dict("records"):
            kind, sl, tp = r["exit_kind"], float(r["sl"]), float(r["tp"])
            if kind not in ("sl", "tp"):
                continue
            f, x = B.bar_at_ms(int(r["fill_msc"])), B.bar_at_ms(int(r["exit_msc"]))
            if x is None:
                continue
            lo = f + 1 if f is not None else x
            long_ = r["dir"] == "L"
            hit = [j for j in range(lo, x)
                   if ((B.l[j] <= sl + cm.EPS or B.h[j] >= tp - cm.EPS) if long_ else B.h[j] >= sl - cm.EPS)]
            if not hit or not all(B.gap_open[j] for j in hit):
                continue
            g = hit[0]
            rows.append(dict(case_id=base + int(r["setup_id"]), run_id=run_id, variant=v.upper(), setup_id=int(r["setup_id"]),
                             dir=r["dir"], sl=sl, tp=tp, fill_msc=int(r["fill_msc"]), fill_price=float(r["fill_price"]),
                             volume=float(r["volume"]), exit_msc=int(r["exit_msc"]), exit_price=float(r["exit_price"]),
                             exit_kind=kind, gap_bar=B.t[g], prev_bar=B.t[g - 1], gap_s=B.t[g] - B.t[g - 1] - 60,
                             gap_bar_low=B.l[g], gap_bar_high=B.h[g], synthetic_month=B.t[g] < 1769904000))
    return pd.DataFrame(rows)


def windows(cs: pd.DataFrame) -> list:
    w = []
    for r in cs.itertuples():
        w.append((r.prev_bar * 1000, (r.gap_bar + PAD_S) * 1000))
        w.append((r.exit_msc - 30_000, r.exit_msc + 5_000))
    w.sort()
    merged = []
    for a, b in w:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def cmd_run(run_ids=PILOT_RUNS, tag=RUN_ID, out=OUT):
    if runner.live_terminal_running():
        raise SystemExit("live MT5 terminal is running; research runs only while it is closed")
    cfg = env.load_config()
    env.disable_mcp(cfg)
    env.assert_trade_safety(cfg)
    cs = cases(run_ids)
    out.mkdir(parents=True, exist_ok=True)
    cs.to_csv(out / "cases.csv", index=False)
    files = cfg.mt5_dir / "MQL5" / "Files"
    files.mkdir(parents=True, exist_ok=True)
    rep = cs.assign(dir=np.where(cs["dir"] == "L", 1, -1), open_from=cs["prev_bar"] * 1000,
                    open_to=cs["gap_bar"] * 1000)[["case_id", "dir", "sl", "tp", "open_from", "open_to"]]
    rep.to_csv(files / "probe_cases.csv", index=False)
    pd.DataFrame(windows(cs), columns=["start_ms", "end_ms"]).to_csv(files / "probe_windows.csv", index=False)
    shutil.copy2(SRC, cfg.mt5_dir / "MQL5" / "Experts" / SRC.name)
    c = compmod.compile_ea(cfg, SRC.name)
    (out / "compile.log").write_text(c["log"])
    if c["errors"] != 0 or not c["ex5_exists"]:
        raise SystemExit(f"probe compile failed: {c['errors']} errors")
    start, end = RUN["windows"]["wfo"]
    text = ini.render(expert="session_probe.ex5", symbol=RUN["symbol"], period="M1", from_date=start,
                      to_date_inclusive=end, deposit=RUN["deposit"], report=f"reports\\{tag}",
                      set_lines=[f"ResearchRunTag={tag}"], leverage=RUN["leverage"], currency=RUN["currency"],
                      model=RUN["model"])
    res = runner.run(cfg, tag, text, "session_probe.ex5", meta={"kind": "probe", "values": {}})
    explog.append({"id": tag, "purpose": "gate review: sessions, ticks, order acceptance and SL/TP replicas at "
                   "quote-gap opens (probe EA, no strategy rules)", "role": "diagnostic", "expert": "session_probe.ex5",
                   "period": "M1", "from": start, "to": end, "status": res.status, "seconds": res.seconds})
    print(res.status, res.seconds, len(cs), "cases")


def _hms(s):
    s = int(s) % 86400
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _t(ms):
    return pd.to_datetime(int(ms), unit="ms").strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def trigger(dir_, sl, tp, bid, ask):
    """MT5 position exits: a buy closes on Bid (SL: Bid <= SL, TP: Bid >= TP); a sell on Ask (SL: Ask >= SL,
    TP: Ask <= TP)."""
    if dir_ == "L":
        return "sl" if bid <= sl + 1e-9 else "tp" if bid >= tp - 1e-9 else None
    return "sl" if ask >= sl - 1e-9 else "tp" if ask <= tp + 1e-9 else None


def cmd_analyze(tag=RUN_ID, out=OUT):
    d = runner.RUNS / tag
    cs = pd.read_csv(out / "cases.csv")
    sess = pd.read_csv(d / f"rl_sessions_{tag}.csv")
    ticks = pd.read_csv(d / f"rl_ticks_{tag}.csv")
    orders = pd.read_csv(d / f"rl_orders_{tag}.csv")
    rep = pd.read_csv(d / f"rl_replica_{tag}.csv")
    days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    sessions = {}
    for r in sess[sess.kind.isin(["quote", "trade"])].itertuples():
        to = "24:00:00" if int(r.to_s) >= 86400 else _hms(r.to_s)
        sessions.setdefault(r.kind, {}).setdefault(days[int(r.weekday)], []).append(f"{_hms(r.from_s)}-{to}")
    spec = {r.kind.replace("spec_", ""): int(r.weekday) for r in sess[sess.kind.str.startswith("spec")].itertuples()}

    orders["minute"] = pd.to_datetime(orders.tick_msc, unit="ms").dt.strftime("%H:%M")
    acc = orders.groupby(["probe", "retcode"]).size().rename("n").reset_index().to_dict("records")
    t = ticks.tick_msc.to_numpy()
    evidence = []
    for r in cs.itertuples():
        g0, g1 = r.gap_bar * 1000, (r.gap_bar + 60) * 1000
        sel = ticks[(t >= r.prev_bar * 1000) & (t < (r.gap_bar + PAD_S) * 1000)]
        trig = [(x.tick_msc, x.bid, x.ask, trigger(r.dir, r.sl, r.tp, x.bid, x.ask)) for x in sel.itertuples()]
        in_gap = [z for z in trig if g0 <= z[0] < g1 and z[3]]
        after = [z for z in trig if z[0] >= g1 and z[3]]
        before = [z for z in trig if z[0] < g0 and z[3]]
        first_gap_tick = sel[sel.tick_msc >= g0].head(1)
        ex = ticks[(t >= r.exit_msc - 30_000) & (t <= r.exit_msc + 5_000)]
        at_exit = ex[ex.tick_msc <= r.exit_msc].tail(1)
        rr = rep[rep.case_id == r.case_id]
        ro = rr[rr.event == "open"].tail(1)
        rc = rr[rr.event == "close"].tail(1)
        evidence.append(dict(
            case=f"{r.variant} #{r.setup_id}", dir=r.dir, sl=r.sl, tp=r.tp, exit_kind=r.exit_kind,
            synthetic_month=bool(r.synthetic_month), gap_bar=_t(r.gap_bar * 1000), quote_gap_min=round(r.gap_s / 60, 1),
            first_tick_after_gap=None if first_gap_tick.empty else
            f"{_t(first_gap_tick.tick_msc.iloc[0])} Bid {first_gap_tick.bid.iloc[0]:.2f} Ask {first_gap_tick.ask.iloc[0]:.2f}",
            trigger_ticks_before_gap=len(before),
            trigger_ticks_in_gap_minute=len(in_gap),
            first_trigger_in_gap=None if not in_gap else
            f"{_t(in_gap[0][0])} Bid {in_gap[0][1]:.2f} Ask {in_gap[0][2]:.2f} ({in_gap[0][3]})",
            first_trigger_after_gap_minute=None if not after else
            f"{_t(after[0][0])} Bid {after[0][1]:.2f} Ask {after[0][2]:.2f} ({after[0][3]})",
            ea_exit=f"{_t(r.exit_msc)} @ {r.exit_price:.2f} ({r.exit_kind})",
            tick_at_ea_exit=None if at_exit.empty else
            f"{_t(at_exit.tick_msc.iloc[0])} Bid {at_exit.bid.iloc[0]:.2f} Ask {at_exit.ask.iloc[0]:.2f} "
            f"-> {trigger(r.dir, r.sl, r.tp, at_exit.bid.iloc[0], at_exit.ask.iloc[0])}",
            exit_is_first_trigger_after_gap_minute=bool(after and abs(after[0][0] - r.exit_msc) <= 1),
            replica_open=None if ro.empty else f"{_t(ro.tick_msc.iloc[0])} rc {int(ro.retcode.iloc[0])}",
            replica_close=None if rc.empty else f"{_t(rc.tick_msc.iloc[0])} @ {rc.price.iloc[0]} {rc.reason.iloc[0]}",
            replica_close_msc=None if rc.empty else int(rc.tick_msc.iloc[0]),
            replica_matches_ea_exit=bool(not rc.empty and abs(int(rc.tick_msc.iloc[0]) - r.exit_msc) <= 1),
        ))
    res = pd.DataFrame(evidence)
    out.mkdir(parents=True, exist_ok=True)
    res.to_csv(out / "case_evidence.csv", index=False)
    summary = {"sessions": sessions, "spec": spec, "order_probe": acc,
               "order_probe_by_minute": orders.groupby(["probe", "minute", "retcode"]).size().rename("n")
               .reset_index().sort_values("n", ascending=False).head(20).to_dict("records"),
               "cases": len(res),
               "cases_with_trigger_in_gap_minute": int((res.trigger_ticks_in_gap_minute > 0).sum()),
               "cases_with_trigger_before_gap": int((res.trigger_ticks_before_gap > 0).sum()),
               "exit_is_first_trigger_after_gap_minute": int(res.exit_is_first_trigger_after_gap_minute.sum()),
               "replica_matches_ea_exit": int(res.replica_matches_ea_exit.sum()),
               "replicas_closed": int(res.replica_close.notna().sum()),
               "synthetic_month_cases": int(res.synthetic_month.sum())}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps(summary, indent=1, default=str))



def cmd_sensitivity(tag=RUN_ID, out=OUT, run_ids=PILOT_RUNS):
    """The pre-registered quote-only-minute sensitivity (mt5r/session_sensitivity.py) for each run of the probe."""
    from mt5r import session_sensitivity as ss
    cs = cases(run_ids)
    ticks = pd.read_csv(runner.RUNS / tag / f"rl_ticks_{tag}.csv")
    alt = ss.alt_exits(cs, ticks)
    res = {}
    for run_id in run_ids:
        run = cm.read_run(runner.RUNS / run_id, run_id)
        mine = alt[alt.case_id.isin(cs.loc[cs.run_id == run_id, "case_id"])]
        r = ss.evaluate(run["setups"], run["deals"], mine)
        if run_id.startswith("pilot"):   # R28: the pilot reports no profit fields - only the size of the effect
            r = {"positions": r["positions"], "affected": r["affected"], "outcome_flips": r["outcome_flips"],
                 "net_delta_pct_of_deposit": round(100 * r["net_delta"] / r["deposit"], 2),
                 "max_dd_closed_delta_pp": round(r["max_dd_closed_pct_sensitivity"] - r["max_dd_closed_pct_primary"], 2)}
        res[run_id] = r
    alt.merge(cs[["case_id", "run_id", "dir", "exit_kind", "exit_price"]], on="case_id").to_csv(
        out / "sensitivity_alt_exits.csv", index=False)
    (out / "sensitivity.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


def cmd_charts(tag=RUN_ID, out=OUT, picks=("A #4368", "A #2916", "A #5617", "A #4403", "B #4590")):
    """Tick charts of chosen cases: Bid and Ask from the last pre-gap bar to 3 minutes after the gap, SL/TP, the
    quote-only minute, the probe orders and, when the exit came later, a second panel around the exit."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ev = pd.read_csv(out / "case_evidence.csv")
    cs = pd.read_csv(out / "cases.csv")
    ticks = pd.read_csv(runner.RUNS / tag / f"rl_ticks_{tag}.csv")
    orders = pd.read_csv(runner.RUNS / tag / f"rl_orders_{tag}.csv")
    (out / "charts").mkdir(parents=True, exist_ok=True)
    paths = []
    for name in picks:
        e = ev[ev.case == name].iloc[0]
        variant, sid = name.split(" #")
        c = cs[(cs.variant == variant) & (cs.setup_id == int(sid))].iloc[0]
        g0 = int(c.gap_bar) * 1000
        late = not bool(e.exit_is_first_trigger_after_gap_minute)
        fig, axes = plt.subplots(1, 2 if late else 1, figsize=(15 if late else 10, 5.2), squeeze=False,
                                 gridspec_kw={"width_ratios": [3, 2] if late else [1]})
        ax = axes[0][0]
        w = ticks[(ticks.tick_msc >= g0 - 120_000) & (ticks.tick_msc < g0 + PAD_S * 1000)]
        pre = w[w.tick_msc < g0]
        post = w[w.tick_msc >= g0]
        x = lambda ms: (ms - g0) / 1000.0  # noqa: E731
        for part in (pre, post):
            ax.step(x(part.tick_msc), part.bid, where="post", color="#2a78d6", lw=0.9, label="Bid" if part is post else None)
            ax.step(x(part.tick_msc), part.ask, where="post", color="#eb6834", lw=0.9, label="Ask" if part is post else None)
        ax.axvspan(0, 60, color="#e34948", alpha=0.08, lw=0)
        ax.text(30, 0.97, "quote-only minute\n(trade session from 01:01)", transform=ax.get_xaxis_transform(),
                fontsize=7.5, color="#e34948", va="top", ha="center")
        ax.axvline(0, color="#6b6a64", lw=0.8, ls=":")
        ax.axvline(60, color="#6b6a64", lw=0.8, ls=":")
        for lvl, col, lab in ((c.sl, "#e34948", "SL"), (c.tp, "#008300", "TP")):
            ax.axhline(lvl, color=col, ls="--", lw=1.1)
            ax.text(ax.get_xlim()[1] if False else x(g0 + PAD_S * 1000), lvl, f" {lab} {lvl:.2f}", color=col, fontsize=8,
                    va="center")
        o = orders[(orders.tick_msc >= g0) & (orders.tick_msc < g0 + 120_000)]
        notes = []
        for r in o.itertuples():
            ok = int(r.retcode) == 10009
            ax.plot([x(r.tick_msc)], [r.ask], marker="o" if ok else "x", color="#1f1f1e", ms=7, mew=2, ls="none")
            notes.append(f"{'o' if ok else 'x'} probe Buy 0.01 at {_t(r.tick_msc)[11:]}: "
                         f"{'filled' if ok else 'refused, retcode 10018 (market closed)'}")
        trig = e.first_trigger_in_gap
        if isinstance(trig, str):
            tms = pd.Timestamp(trig.split(" Bid")[0]).value // 10**6
            px = float(trig.split("Ask ")[1].split(" ")[0]) if c.dir == "S" else float(trig.split("Bid ")[1].split(" ")[0])
            ax.plot([x(tms)], [px], marker="v", color="#4a3aa7", ms=9, ls="none")
            ax.annotate(f"first {'Ask' if c.dir == 'S' else 'Bid'} at the level\n{trig.split(' Bid')[0][11:]} (no execution)",
                        (x(tms), px), xytext=(16, -36), textcoords="offset points", fontsize=7, color="#4a3aa7",
                        arrowprops=dict(arrowstyle="-", color="#4a3aa7", lw=0.6))
        if not late:
            ax.plot([x(c.exit_msc)], [c.exit_price], marker="X", color="#1f1f1e", ms=10, ls="none")
            ax.annotate(f"tester exit ({c.exit_kind}) {_t(c.exit_msc)[11:]} @ {c.exit_price:.2f}\n= replica exit",
                        (x(c.exit_msc), c.exit_price), xytext=(8, 4), textcoords="offset points", fontsize=7)
        ax.set_xlabel("seconds from the 01:00 quote open (server time); the gap before it is cut out\n"
                      + "\n".join(notes), fontsize=8)
        ax.set_ylabel("price", fontsize=8)
        ax.legend(fontsize=7, loc="lower left")
        ax.set_title(f"{name} {'LONG' if c.dir == 'L' else 'SHORT'} - session open {_t(g0)[:16]}"
                     f"{' (generated-tick month)' if c.synthetic_month else ' (real ticks)'}", fontsize=9, loc="left")
        if late:
            a2 = axes[0][1]
            w2 = ticks[(ticks.tick_msc >= c.exit_msc - 30_000) & (ticks.tick_msc <= c.exit_msc + 5_000)]
            x2 = lambda ms: (ms - c.exit_msc) / 1000.0  # noqa: E731
            a2.step(x2(w2.tick_msc), w2.bid, where="post", color="#2a78d6", lw=0.9)
            a2.step(x2(w2.tick_msc), w2.ask, where="post", color="#eb6834", lw=0.9)
            lvl = c.sl if c.exit_kind == "sl" else c.tp
            a2.axhline(lvl, color="#e34948" if c.exit_kind == "sl" else "#008300", ls="--", lw=1.1)
            a2.plot([0], [c.exit_price], marker="X", color="#1f1f1e", ms=10, ls="none")
            a2.annotate(f"tester exit ({c.exit_kind}) {_t(c.exit_msc)[11:]} @ {c.exit_price:.2f}\n= replica exit",
                        (0, c.exit_price), xytext=(-150, 10), textcoords="offset points", fontsize=7)
            a2.set_xlabel("seconds from the actual exit", fontsize=8)
            a2.set_title(f"actual exit {_t(c.exit_msc)[:19]}", fontsize=9, loc="left")
        fig.tight_layout()
        p = out / "charts" / f"{name.replace(' #', '_')}.png"
        fig.savefig(p, dpi=110)
        plt.close(fig)
        paths.append(str(p))
    print("\n".join(paths))


if __name__ == "__main__":
    {"run": cmd_run, "analyze": cmd_analyze, "sensitivity": cmd_sensitivity, "charts": cmd_charts}[sys.argv[1]]()
