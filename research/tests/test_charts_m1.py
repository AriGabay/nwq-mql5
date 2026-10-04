"""M5 + M1 setup charts and per-setup table for the chart gate (plan 2026-10-03-0013, U5, R26, KTD12).

All data is synthetic and built here: one M1 price path (07:00-12:00 server time), its M5 aggregate, the causal
pivot sequence of that path, and setups/events/deals written in the m1_contract column layout.
"""
import datetime as dt
import gzip
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from mt5r import charts_m1 as cm  # noqa: E402
from mt5r import m1_contract as mc  # noqa: E402

BASE = int(dt.datetime(2026, 3, 2, 7, 0, tzinfo=dt.timezone.utc).timestamp())   # server time as epoch seconds
N = 3   # SwingStrengthM1


def t(minute):
    return BASE + 60 * minute


def ms(minute, second=0):
    return (t(minute) + second) * 1000


# --- synthetic market ------------------------------------------------------------------------------------------
WAYPOINTS = [(0, 2000.0), (5, 2001.2), (10, 1997.4), (15, 2000.2), (20, 2005.6), (25, 2007.2), (45, 2009.5),
             (70, 2006.0), (95, 2010.2), (118, 2001.6), (124, 1999.0), (128, 1996.2), (133, 1998.6),
             (140, 2001.4), (146, 1999.6), (152, 2003.2), (156, 2006.2), (162, 2003.4), (165, 2001.7),
             (167, 2002.9), (172, 2005.5), (190, 2010.0), (205, 2016.5), (230, 2013.0), (299, 2011.0)]


def m1_bars():
    rng = np.random.default_rng(5)
    mins = np.arange(300)
    close = np.interp(mins + 1, *zip(*WAYPOINTS)) + rng.normal(0, 0.12, 300)
    opn = np.r_[WAYPOINTS[0][1], close[:-1]]
    wick = np.abs(rng.normal(0.25, 0.1, 300))
    return pd.DataFrame({"time": [t(m) for m in mins], "open": opn.round(2),
                         "high": (np.maximum(opn, close) + wick).round(2),
                         "low": (np.minimum(opn, close) - wick[::-1]).round(2), "close": close.round(2),
                         "tick_volume": 100, "spread": 20, "warmup": 0})


def m5_bars(m1):
    g = m1.groupby((m1["time"] - BASE) // 300)
    return pd.DataFrame({"time": g["time"].first(), "open": g["open"].first(), "high": g["high"].max(),
                         "low": g["low"].min(), "close": g["close"].last(), "tick_volume": g["tick_volume"].sum(),
                         "spread": 20, "warmup": 0}).reset_index(drop=True)


def pivots_of(m1):
    """Strict N-bar pivots in one alternating sequence (R9): of two consecutive same-type pivots the more extreme
    is kept, the other gets replaced_by."""
    h, lo = m1["high"].to_numpy(), m1["low"].to_numpy()
    seq, rows = [], []
    for p in range(N, len(m1) - N):
        for typ, arr, better in (("H", h, np.greater), ("L", lo, np.less)):
            side = np.r_[arr[p - N:p], arr[p + 1:p + N + 1]]
            if not better(arr[p], side).all():
                continue
            row = {"pivot_id": len(rows) + 1, "type": typ, "peak_time": t(p), "conf_time": t(p + N),
                   "level": arr[p], "replaced_by": None, "outside_bar": 0}
            rows.append(row)
            if seq and seq[-1]["type"] == typ:
                if better(arr[p], seq[-1]["level"]):
                    seq[-1]["replaced_by"] = row["pivot_id"]
                    seq[-1] = row
                else:
                    row["replaced_by"] = seq[-1]["pivot_id"]
                continue
            seq.append(row)
    return pd.DataFrame(rows, columns=mc.PIVOT_COLUMNS)


def setup_row(**kw):
    row = dict.fromkeys(mc.SETUP_COLUMNS)
    row.update(kw)
    return row


def event(sid, seq, kind, **kw):
    row = dict.fromkeys(mc.EVENT_COLUMNS)
    row.update(setup_id=sid, seq=seq, kind=kind, **kw)
    return row


def long_stages(m1, pv, ob_high, ob_low, after):
    """Bar indices of a long setup's stages on the synthetic path (R5-R14 simplified, enough for a chart)."""
    o, h, lo, c = (m1[k].to_numpy() for k in ("open", "high", "low", "close"))
    idx = np.arange(len(m1))
    touch = int(idx[(idx >= after) & (lo <= ob_high)][0])
    brk = int(idx[(idx >= touch) & (c < ob_low)][0])
    ret = int(idx[(idx > brk) & (h >= ob_low)][0])
    live = pv[pv["replaced_by"].isna()]
    hh = ref = None
    for i in range(touch, len(m1)):
        highs = live[(live["type"] == "H") & (live["conf_time"] <= t(i))]
        if len(highs) and c[i] > highs.iloc[-1]["level"]:
            hh, ref = i, highs.iloc[-1]
            break
    ref_peak = (int(ref["peak_time"]) - BASE) // 60
    origin = ref_peak + int(np.argmin(lo[ref_peak:hh + 1]))
    c1 = [k for k in range(origin, hh) if lo[k + 2] > h[k]][-1]
    hl = live[(live["type"] == "L") & (live["peak_time"] > t(hh))].iloc[0]
    zone_hi = lo[c1 + 2]
    react = int(idx[(idx > max(hh, c1 + 2, ret)) & (lo <= zone_hi) & (c > o) & (c > zone_hi)][0])
    return dict(touch=touch, brk=brk, ret=ret, hh=hh, ref=ref, origin=origin, c1=c1, hl=hl, react=react)


def scenario():
    """Setup 1: variant A long on the 07:05 M5 OB, broken and returned, HH, entry FVG, HL, reaction, TP exit.
    Setup 2: the same zone on variant B, cancelled by a second break in the bar of its return (KTD6)."""
    m1 = m1_bars()
    m5 = m5_bars(m1)
    pv = pivots_of(m1)
    ob, c1, c3 = m5.iloc[1], m5.iloc[2], m5.iloc[4]    # 07:05 red candle, identifying FVG 07:10-07:20
    ob_hi, ob_lo = float(ob["high"]), float(ob["low"])
    s = long_stages(m1, pv, ob_hi, ob_lo, after=25)
    ref, hl, f1, react = s["ref"], s["hl"], s["c1"], s["react"]
    fvg_lo, fvg_hi = float(m1.iloc[f1]["high"]), float(m1.iloc[f1 + 2]["low"])
    close = m1["close"].to_numpy()
    fill_px = round(float(m1.iloc[react + 1]["open"]) + 0.03, 2)
    anchor_px, anchor = min((ob_lo, "ob"), (float(hl["level"]), "hl"))
    sl = round(anchor_px - 0.20, 2)
    tp = round(fill_px + 2 * (fill_px - sl), 2)
    exit_min = int(np.argmax((m1["high"].to_numpy() >= tp) & (np.arange(300) > react)))
    assert exit_min > react, "the synthetic path must reach TP"
    zone = dict(dir="L", ob_time=int(ob["time"]), ob_high=ob_hi, ob_low=ob_lo, idfvg_c1_time=int(c1["time"]),
                idfvg_c3_time=int(c3["time"]), idfvg_low=float(c1["high"]), idfvg_high=float(c3["low"]),
                identified_in_warmup=0, touch_msc=ms(s["touch"], 20), touch_bar_time=t(s["touch"]))
    rows = [setup_row(setup_id=1, variant=0, **zone, breaks=1, returns=1, sc_bar_time=t(s["hh"]),
                      origin_time=t(s["origin"]), ref_pivot_id=int(ref["pivot_id"]), hl_pivot_id=int(hl["pivot_id"]),
                      fvg_c1_time=t(f1), fvg_low=fvg_lo, fvg_high=fvg_hi, reaction_bar_time=t(react),
                      entry_request_msc=ms(react + 1, 1), request_price=fill_px - 0.03, attempts=1, sl=sl,
                      sl_anchor=anchor, sl_anchor_price=anchor_px, buffer_pts=20, tp=tp, volume=0.05,
                      fill_msc=ms(react + 1, 1), fill_price=fill_px, position_id=501, exit_msc=ms(exit_min, 30),
                      exit_price=tp, exit_kind="tp", reason="filled", reason_msc=ms(exit_min, 30)),
            setup_row(setup_id=2, variant=1, **zone, breaks=2, returns=1, reason="cancelled_second_break",
                      reason_msc=ms(s["ret"] + 1))]
    ev = [event(1, 1, "touch", bar_time=t(s["touch"]), tick_msc=ms(s["touch"], 20), price=ob_hi),
          event(1, 2, "break", bar_time=t(s["brk"]), price=float(close[s["brk"]])),
          event(1, 3, "return", bar_time=t(s["ret"]), tick_msc=ms(s["ret"], 40), price=ob_lo),
          event(1, 4, "sc_hh", bar_time=t(s["hh"]), price=float(ref["level"]), ref_id=int(ref["pivot_id"]),
                ref_time=t(s["origin"])),
          event(1, 5, "fvg_fixed", bar_time=t(max(f1 + 2, s["hh"] + 1)), ref_time=t(f1), lo=fvg_lo, hi=fvg_hi),
          event(1, 6, "hl", bar_time=int(hl["conf_time"]), ref_id=int(hl["pivot_id"]), price=float(hl["level"])),
          event(1, 7, "reaction", bar_time=t(react)),
          event(1, 8, "entry_attempt", tick_msc=ms(react + 1, 1), price=fill_px - 0.03, detail="10009"),
          event(1, 9, "fill", tick_msc=ms(react + 1, 1), price=fill_px, lo=sl, hi=tp),
          event(1, 10, "exit", tick_msc=ms(exit_min, 30), price=tp, detail="tp"),
          event(2, 1, "touch", bar_time=t(s["touch"]), tick_msc=ms(s["touch"], 20), price=ob_hi),
          event(2, 2, "break", bar_time=t(s["brk"]), price=float(close[s["brk"]])),
          event(2, 3, "return", bar_time=t(s["ret"]), tick_msc=ms(s["ret"], 10), price=ob_lo),
          event(2, 4, "cancelled_second_break", bar_time=t(s["ret"]), price=float(close[s["ret"]]))]
    deals = pd.DataFrame([
        ("2026.03.01 00:00:00", 1, 0, 2, 0, 0.0, 0.0, 10000.0, 0.0, 0.0, 0, ""),
        ("2026.03.02 09:48:01", 2, 501, 0, 0, 0.05, fill_px, 0.0, -0.2, 0.0, 770201, "OBM1"),
        ("2026.03.02 10:30:00", 3, 501, 1, 1, 0.05, tp, 61.5, -0.2, 0.0, 770201, "")], columns=mc.DEALS_COLUMNS)
    return {"setups": pd.DataFrame(rows, columns=mc.SETUP_COLUMNS),
            "events": pd.DataFrame(ev, columns=mc.EVENT_COLUMNS), "pivots": pv, "bars_m1": m1, "bars_m5": m5,
            "deals": deals}


def write_run(folder, data, tag="T1", gz=("bars_m1",)):
    """Contract CSVs as the EA writes them (rl_<name>_<tag>.csv), the names in ``gz`` gzipped as curation keeps them."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, cols in mc.FILES.items():
        if name not in data:
            continue
        text = data[name][cols].to_csv(index=False, lineterminator="\n")
        if name in gz:
            (folder / f"rl_{name}_{tag}.csv.gz").write_bytes(gzip.compress(text.encode()))
        else:
            (folder / f"rl_{name}_{tag}.csv").write_text(text, encoding="utf-8")


def load_scenario(tmp_path):
    write_run(tmp_path / "run", scenario())
    return cm.load_run(tmp_path / "run", "T1")


# --- a catalogue with every KTD12 category, both variants and both sides --------------------------------------
def catalog(drop=()):
    """Three candidates per (variant, side, category); ``drop`` removes whole (variant, dir, category) pools."""
    setups, events, deals, sid = [], [], [], 0
    for v in (0, 1):
        for d in ("L", "S"):
            for cat in cm.CATEGORIES:
                if (cm.variant_name(v), d, cat) in drop:
                    continue
                group = []
                for k in range(3):
                    sid += 1
                    base = dict(setup_id=sid, dir=d, variant=v, ob_time=t(5), ob_high=2001.0, ob_low=1997.0,
                                touch_msc=ms(118), touch_bar_time=t(118), breaks=0, returns=0)
                    ev = [event(sid, 1, "touch", bar_time=t(118), tick_msc=ms(118), price=2001.0)]
                    if cat == "broken_returned":
                        base.update(breaks=1, returns=1, reason="run_end_waiting")
                        ev += [event(sid, 2, "break", bar_time=t(127)), event(sid, 3, "return", bar_time=t(131))]
                    elif cat in ("second_break_cancel", "return_break_same_bar"):
                        ret = 140 if cat == "return_break_same_bar" else 131
                        base.update(breaks=2, returns=1, reason="cancelled_second_break", reason_msc=ms(141))
                        ev += [event(sid, 2, "break", bar_time=t(127)), event(sid, 3, "return", bar_time=t(ret)),
                               event(sid, 4, "cancelled_second_break", bar_time=t(140))]
                    elif cat == "opposing_structure_cancel":
                        base.update(reason="cancelled_opposing_structure", reason_msc=ms(150))
                        ev += [event(sid, 2, "cancelled_opposing_structure", bar_time=t(149), ref_id=3)]
                    elif cat in ("winner", "loser", "shared_structure", "far_from_ob"):
                        pos = 1000 + sid
                        # stacked: one structure per group (same sc_hh bar and FVG), later members lost R16 first
                        sc = 152 if cat == "shared_structure" else 150 + sid % 7
                        react = 167 + (k if cat == "shared_structure" else 0)
                        base.update(reason="filled", position_id=pos, sc_bar_time=t(sc), fvg_c1_time=t(sc - 2),
                                    fvg_low=2002.0, fvg_high=2003.0 + 0.01 * (0 if cat == "shared_structure" else sid),
                                    reaction_bar_time=t(react), fill_msc=ms(react + 1),
                                    fill_price=(2040.0 if d == "L" else 1960.0) if cat == "far_from_ob" else 2004.0,
                                    sl=1996.8, tp=2018.4, exit_msc=ms(200), exit_kind="tp" if cat != "loser" else "sl")
                        ev += [event(sid, 2, "sc_hh", bar_time=t(sc), ref_id=2)]
                        if cat == "shared_structure" and k > 0:
                            ev += [event(sid, 3, "reaction", bar_time=t(167)),
                                   event(sid, 4, "lost_competition", bar_time=t(167), ref_id=group[0])]
                        ev += [event(sid, 5, "reaction", bar_time=t(react)),
                               event(sid, 6, "fill", tick_msc=ms(react + 1))]
                        net = -40.0 if cat == "loser" else 80.0
                        deals.append(("2026.03.02 10:00:00", 2 * sid, pos, 0, 0, 0.05, 2004.0, 0.0, -0.2, 0.0, 1,
                                      ""))
                        deals.append(("2026.03.02 11:00:00", 2 * sid + 1, pos, 1, 1, 0.05, 2010.0, net, -0.2, 0.0,
                                      1, ""))
                    group.append(sid)
                    setups.append(setup_row(**base))
                    events += ev
    d = pd.DataFrame(deals, columns=mc.DEALS_COLUMNS)
    d["time"] = pd.to_datetime(d["time"], format="%Y.%m.%d %H:%M:%S")
    return pd.DataFrame(setups, columns=mc.SETUP_COLUMNS), pd.DataFrame(events, columns=mc.EVENT_COLUMNS), d


def picks(sel):
    return list(zip(sel["variant"].map(cm.variant_name), sel["dir"], sel["category"], sel["setup_id"].astype(int)))


# --- U5 test scenarios -----------------------------------------------------------------------------------------
def test_each_category_picks_deterministically_under_the_seed():
    setups, events, deals = catalog()
    a = cm.select_examples(setups, events, deals)
    b = cm.select_examples(setups.sample(frac=1, random_state=3), events.sample(frac=1, random_state=4),
                           deals.sample(frac=1, random_state=5), seed=20260930)
    assert picks(a) == picks(b)
    assert a.attrs["missing"] == []
    assert {(v, d, c) for v, d, c, _ in picks(a)} == {(v, d, c) for v in "AB" for d in "LS" for c in cm.CATEGORIES}
    assert len(a) == len(set(a["setup_id"]))
    rows = {(v, d, c): r for (v, d, c, _), r in zip(picks(a), a.to_dict("records"))}
    assert all(rows[(v, d, "winner")]["net"] > 0 and rows[(v, d, "loser")]["net"] < 0 for v in "AB" for d in "LS")
    assert all(rows[(v, d, "return_break_same_bar")]["reason"] == "cancelled_second_break" for v in "AB" for d in "LS")
    stacked = rows[("A", "L", "shared_structure")]
    assert stacked["related"]                                   # names the other fills of its structure
    far = rows[("A", "S", "far_from_ob")]
    assert cm.entry_distance(far) == 1997.0 - 1960.0               # short: OB low - fill
    other = cm.select_examples(setups, events, deals, seed=1)
    assert picks(other) != picks(a)   # the seed, not the input order, decides


def test_a_category_without_candidates_is_listed_as_missing():
    setups, events, deals = catalog(drop={("B", "S", "opposing_structure_cancel"), ("A", "L", "shared_structure"),
                                          ("A", "L", "return_break_same_bar")})
    sel = cm.select_examples(setups, events, deals)
    assert set(sel.attrs["missing"]) == {"B_short_opposing_structure_cancel", "A_long_shared_structure",
                                         "A_long_return_break_same_bar"}
    assert ("B", "S", "opposing_structure_cancel") not in {(v, d, c) for v, d, c, _ in picks(sel)}
    only_a = cm.select_examples(setups[setups["variant"] == 0], events, deals)
    assert not any(m.startswith("B_") for m in only_a.attrs["missing"])   # only variants present are listed


def test_render_writes_pngs_and_one_table_row_per_example(tmp_path):
    run = load_scenario(tmp_path)     # bars_m1 comes from a .csv.gz
    assert len(run["bars_m1"]) == 300 and run["setups"]["setup_id"].tolist() == [1, 2]
    out = cm.render(run, tmp_path / "out")
    assert set(out) >= {"charts", "table", "missing", "examples"}
    assert len(out["charts"]) == len(out["examples"]) >= 3
    for p in map(pathlib.Path, out["charts"]):
        assert p.parent == tmp_path / "out" / "charts"
        assert p.exists() and p.read_bytes()[:4] == b"\x89PNG" and p.stat().st_size > 20000
    text = (tmp_path / "out" / "setups_table.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith("| setup"))
    body = [line for line in lines[header + 2:] if line.startswith("| ")]
    assert len(body) == len(out["examples"])
    for col in ("variant", "dir", "category", "OB", "touch", "breaks", "returns", "structure change", "HL/LH",
                "entry FVG", "reaction", "fill", "SL", "SL anchor", "TP", "exit", "net", "reason"):
        assert col in lines[header], col
    touch = int(run["setups"].loc[0, "touch_msc"])
    stamp = dt.datetime.fromtimestamp(touch / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000")
    assert stamp in text                                # touch tick, server time with ms
    assert "+61.10" in text                             # net = profit + commission over the position
    assert "Missing categories:" in text and "B_long_winner" in out["missing"]
    assert "seed 20260930" in text


def test_m5_window_spans_ob_to_exit_and_m1_window_follows_ktd12(tmp_path):
    run = load_scenario(tmp_path)
    s, ev, pv = run["setups"], run["events"], run["pivots"]
    win = s[s.setup_id == 1].iloc[0]
    w = cm.windows(win, ev, pv)
    assert w["m5"] == (t(5), int(win["exit_msc"]) // 1000)          # OB candle open to the exit tick
    ref_peak = int(pv.loc[pv.pivot_id == int(win["ref_pivot_id"]), "peak_time"].iloc[0])
    touch_s = int(win["touch_msc"]) // 1000
    assert ref_peak > touch_s - 1800
    assert w["m1"] == (touch_s - 1800, w["m5"][1])                    # 30 minutes before the touch tick
    # a referenced pivot older than touch - 30 min moves the M1 start back to its peak
    early = int(pv[(pv.type == "H") & (pv.peak_time < t(80))].iloc[-1]["pivot_id"])
    ev2 = ev.copy()
    ev2.loc[(ev2.setup_id == 1) & (ev2.kind == "sc_hh"), "ref_id"] = early
    early_peak = int(pv.loc[pv.pivot_id == early, "peak_time"].iloc[0])
    assert cm.windows(win, ev2, pv)["m1"] == (early_peak, w["m5"][1])
    # a cancelled setup ends at its cancellation
    cancel = s[s.setup_id == 2].iloc[0]
    wc = cm.windows(cancel, ev, pv)
    assert wc["m5"] == (t(5), int(cancel["reason_msc"]) // 1000)
    assert wc["m1"] == (touch_s - 1800, wc["m5"][1])
    # the drawn panels cover exactly those windows (plus a small margin of whole bars)
    fig, (ax5, ax1) = plt.subplots(2, 1)
    info = cm.draw_setup(ax5, ax1, win, run, net=61.1, category="winner")
    assert info["m5_bars"][0] <= t(5) and info["m5_bars"][1] >= w["m5"][1] - 300
    assert info["m1_bars"][0] == (w["m1"][0] // 60) * 60 - 60 * cm.M1_PAD
    assert info["m1_bars"][1] >= w["m1"][1] - 60
    assert {"OB", "identifying FVG", "touch", "M1 window", "pivots", "reference level", "HH", "HL", "entry FVG",
            "reaction", "break", "return", "fill", "SL", "TP", "exit"} <= set(info["drawn"])
    plt.close(fig)


def test_m1_window_stops_an_hour_after_the_fill_when_the_exit_is_later(tmp_path):
    """A position held for hours makes the M1 panel unreadable; the M1 panel then ends one hour after the fill and
    the exit stays on the M5 panel (seen on the U7 pilot charts)."""
    run = load_scenario(tmp_path)
    s, ev, pv = run["setups"], run["events"], run["pivots"]
    win = s[s.setup_id == 1].iloc[0].copy()
    fill_s = int(win["fill_msc"]) // 1000
    win["exit_msc"] = (fill_s + 5 * 3600) * 1000                      # exit five hours after the fill
    ev = ev.copy()
    ev.loc[(ev.setup_id == 1) & (ev.kind == "exit"), "tick_msc"] = (fill_s + 5 * 3600) * 1000
    w = cm.windows(win, ev, pv)
    assert w["m5"][1] == fill_s + 5 * 3600
    assert w["m1"][1] == fill_s + 3600


def test_timeline_lists_events_in_the_order_they_became_known(tmp_path):
    """The chart-gate review asked for event and confirmation times, not only positions: bar events are known at
    the close of their bar, tick events at their tick, pivots at the close of their confirmation bar."""
    run = load_scenario(tmp_path)
    s, ev, pv = run["setups"], run["events"], run["pivots"]
    win = s[s.setup_id == 1].iloc[0]
    items = cm.timeline(win, cm._events_by_setup(ev)[1], pv)
    known = [it["known_ms"] for it in items]
    assert known == sorted(known)
    whats = [it["what"] for it in items]
    assert whats[0].startswith("OB identified") and "fill" in whats and whats[-1].startswith("exit")
    sc = next(it for it in items if it["what"] == "structure change")
    sc_bar = int(ev[(ev.setup_id == 1) & (ev.kind == "sc_hh")]["bar_time"].iloc[0])
    assert sc["known_ms"] == (sc_bar + 60) * 1000 and "confirmed at the" in sc["note"]
    fig, (ax5, ax1, axt) = plt.subplots(3, 1)
    info = cm.draw_setup(ax5, ax1, win, run, ax_tl=axt)
    assert "timeline" in info["drawn"] and [it["n"] for it in info["timeline"]] == list(range(1, len(items) + 1))
    plt.close(fig)
