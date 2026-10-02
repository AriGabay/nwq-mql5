"""Pilot setup charts and per-setup table for the R39 chart gate (plan U6, KTD11)."""
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from mt5r import charts_setups as cs  # noqa: E402
from mt5r import conformance as cf  # noqa: E402
from mt5r import reports  # noqa: E402

FX = pathlib.Path(__file__).parent / "fixtures" / "ob_fvg"
P = 900


def load(name="rl_setups_mixed.csv"):
    return cf.read_setups(FX / name), cf.read_bars(FX / "rl_bars.csv"), reports.read_deals(FX / "rl_deals.csv")


def test_render_writes_pngs_and_a_table_with_every_stage_label(tmp_path):
    setups, bars, deals = load()
    out = cs.render(setups, bars, deals, tmp_path, P)
    assert set(out) == {"charts", "table", "missing"}
    assert len(out["charts"]) == 4  # long winner, short loser, invalidated_touched, skipped_cap
    for p in out["charts"]:
        p = pathlib.Path(p)
        assert p.exists() and p.read_bytes()[:4] == b"\x89PNG" and p.stat().st_size > 5000
    text = pathlib.Path(out["table"]).read_text()
    header = next(line for line in text.splitlines() if line.startswith("| setup"))
    for label in cs.STAGE_LABELS:
        assert label in header, label
    assert len([line for line in text.splitlines() if line.startswith("| ") and "---" not in line]) == 5
    for reason in ("filled", "invalidated_touched", "skipped_cap"):
        assert reason in text
    assert "planned RR" in text and "realized R" in text and "net after costs" in text
    assert "194.60" in text and "-97.90" in text  # profit + commission + swap per position
    assert "2003.80" in text  # intended entry and fill price of the long
    assert set(out["missing"]) == {"long_loser", "short_winner"} | {m for m in out["missing"] if "shortfall" in m}


def test_chart_draws_every_stage_marker_and_reason():
    setups, bars, deals = load()
    fig, ax = plt.subplots()
    row = setups[setups.setup_id == 1].iloc[0]
    drawn = cs.draw_setup(ax, row, bars, P, net=194.60)
    assert set(cs.STAGE_LABELS) <= set(drawn)
    title = ax.get_title(loc="left")
    assert "LONG" in title and "filled" in title and "+194.60" in title and "RR 2.00" in title
    assert "2003.80" in title  # planned vs actual fill
    assert "idFVG vol 3.07x" in title and "cFVG vol 3.16x (info)" in title  # AMENDMENT A1 ratios from the setup row
    assert "idFVG vol 3.07x (info)" not in title  # AMENDMENT B: only the cFVG ratio is informational
    plt.close(fig)


def test_table_has_the_fvg_volume_ratio_columns(tmp_path):
    setups, bars, deals = load()
    out = cs.render(setups, bars, deals, tmp_path, P)
    text = pathlib.Path(out["table"]).read_text()
    header = next(line for line in text.splitlines() if line.startswith("| setup"))
    cols = [c.strip() for c in header.strip("|").split("|")]
    assert "idFVG vol ratio" in cols and "cFVG vol ratio (info)" in cols
    assert "no volume filter (AMENDMENT D)" in text  # both ratios informational
    row1 = next(line for line in text.splitlines() if line.startswith("| 1 |"))
    cells = [c.strip() for c in row1.strip("|").split("|")]
    assert cells[cols.index("idFVG vol ratio")] == "3.07" and cells[cols.index("cFVG vol ratio (info)")] == "3.16"
    row3 = next(line for line in text.splitlines() if line.startswith("| 3 |"))  # invalidated before any cFVG
    cells = [c.strip() for c in row3.strip("|").split("|")]
    assert cells[cols.index("cFVG vol ratio (info)")] == "-"


def test_volume_panel_highlights_both_middle_candles():
    setups, bars, deals = load()
    fig, (ax, vax) = plt.subplots(2, 1, sharex=True)
    row = setups[setups.setup_id == 1].iloc[0]
    drawn = cs.draw_setup(ax, row, bars, P, net=194.60, vax=vax)
    assert "tick volume" in drawn
    heights = {round(p.get_x() + p.get_width() / 2): p.get_height() for p in vax.patches}
    assert heights[4] == 330 and heights[10] == 340  # identifying and confirmation FVG middle candles
    colors = {round(p.get_x() + p.get_width() / 2): p.get_facecolor() for p in vax.patches}
    assert colors[4] != colors[5] and colors[10] != colors[9]
    labels = {a.get_text() for a in vax.texts}
    assert "idFVG 3.07x" in labels and "cFVG 3.16x (info)" in labels
    plt.close(fig)


def test_bars_without_tick_volume_still_render(tmp_path):
    setups, bars, deals = load()
    out = cs.render(setups, bars.drop(columns=["tick_volume"]), deals, tmp_path, P)
    assert len(out["charts"]) == 4


def _many(n=20):
    setups, _, _ = load("rl_setups.csv")
    base = setups.iloc[0]
    rows, deals = [], [("2026.01.01 00:00:00", 1, 0, 2, 0, 0.0, 0.0, 10000.0, 0.0, 0.0, 0, "")]
    for i in range(n):
        r = base.copy()
        sid, pos = 100 + i, 9000 + i
        r["setup_id"], r["position_id"], r["order_ticket"] = sid, pos, pos
        r["dir"] = "L" if i % 2 == 0 else "S"
        rows.append(r)
        profit = 50.0 if i % 3 == 0 else -40.0
        deals.append(("2026.01.02 00:00:00", 2 + 2 * i, pos, 0, 0, 0.1, 2000.0, 0.0, 0.0, 0.0, 770101, "OBR"))
        deals.append(("2026.01.02 01:00:00", 3 + 2 * i, pos, 1, 1, 0.1, 2001.0, profit, -0.5, 0.0, 770101, ""))
    cols = "time,ticket,position_id,type,entry,volume,price,profit,commission,swap,magic,comment".split(",")
    d = pd.DataFrame(deals, columns=cols)
    d["time"] = pd.to_datetime(d["time"], format="%Y.%m.%d %H:%M:%S")
    return pd.DataFrame(rows).reset_index(drop=True), d


def test_selection_is_deterministic_for_a_seed_and_covers_winners_and_losers():
    setups, deals = _many()
    a = cs.select_examples(setups, deals, seed=11, n_per_side=3)
    b = cs.select_examples(setups.sample(frac=1, random_state=3), deals, seed=11, n_per_side=3)
    assert a["setup_id"].tolist() == b["setup_id"].tolist()
    assert set(a["category"]) == {"long_winner", "long_loser", "short_winner", "short_loser"}
    assert (a["dir"] == "L").sum() == 3 and (a["dir"] == "S").sum() == 3
    assert a.attrs["missing"] == ["rejections"]  # only filled rows in this set


def test_only_losing_longs_reports_missing_categories(tmp_path):
    setups, bars, deals = load("rl_setups.csv")
    setups = setups[setups.setup_id == 1].reset_index(drop=True)
    deals = deals.copy()
    deals.loc[deals.position_id == 5001, "profit"] = -60.0
    sel = cs.select_examples(setups, deals)
    assert sel["category"].tolist() == ["long_loser"]
    out = cs.render(setups, bars, deals, tmp_path, P)
    missing = set(out["missing"])
    assert {"long_winner", "short_winner", "short_loser"} <= missing and "long_loser" not in missing
    assert any("shortfall" in m for m in missing)  # 1 of 3 longs, 0 of 3 shorts
    assert len(out["charts"]) == 1
