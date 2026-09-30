import pathlib


from mt5r import journal, reports

FX = pathlib.Path(__file__).parent / "fixtures"


def test_trades_report_parses_inputs_metrics_and_deals():
    r = reports.parse_html(FX / "report_trades.htm")
    assert r["inputs"]["VolumeMultiplier"] == "1.2" and r["inputs"]["SweepToSetupBars"] == "24"
    assert r["header"]["Leverage"] == "1:100" and r["header"]["History Quality"] == "100% real ticks"
    s = reports.summary(r)
    assert s["trades"] == 60 and abs(s["net_profit"] - (-2007.52)) < 1e-6
    assert abs(s["equity_dd_pct"] - 22.5) < 0.05
    d = r["deals"]
    assert len(d) == 121 and set(d.Type) >= {"balance", "buy", "sell"}
    trade_deals = d[d.Type.isin(["buy", "sell"])]
    assert abs(trade_deals.Profit.sum() + trade_deals.Swap.sum() + trade_deals.Commission.sum() - (-2007.52)) < 1e-6


def test_no_trades_report():
    r = reports.parse_html(FX / "report_notrades.htm")
    assert reports.summary(r)["trades"] == 0 and r["inputs"]["SignalTF"] == "15"


def test_research_deals_rebuild_positions_and_balance():
    deals = reports.read_deals(FX / "rl_deals.csv")
    tr = reports.trades_from_deals(deals, 10000.0)
    assert len(tr) == 60
    assert abs((tr.profit + tr.commission + tr.swap).sum() - (-2007.52)) < 1e-6
    assert tr.balance_at_open.iloc[0] == 10000.0
    # two same-bar clones open with identical balance
    assert tr.open_time.iloc[0] == tr.open_time.iloc[1]


def test_days_csv():
    d = reports.read_days(FX / "rl_days.csv")
    assert list(d.columns) == ["date", "bal_open", "eq_open", "eq_min", "eq_max", "bal_close", "eq_close", "spread_median"]
    assert (d.eq_min <= d.eq_max).all()


def test_opt_xml_requires_columns(tmp_path):
    x = ('<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet><Table>'
         '<Row><Cell><Data>Pass</Data></Cell><Cell><Data>Profit</Data></Cell></Row>'
         '<Row><Cell><Data>1</Data></Cell><Cell><Data>5.0</Data></Cell></Row></Table></Worksheet></Workbook>')
    p = tmp_path / "o.xml"
    p.write_text(x)
    import pytest
    with pytest.raises(ValueError, match="lacks columns"):
        reports.parse_opt_xml(p)


def test_journal_facts_and_redaction():
    text = ("XAUUSD.s : 2026.01.01 00:00 - 2026.09.15 00:00  real ticks discarded for 3590 minutes of 248304 total "
            "minute bars, every tick generation used\nreal ticks discarded for 2 whole days\n"
            "SweepOB initialised. Warm-up bars: 3000, tick 0.01, stops level 20 pts")
    f = journal.facts(text)
    assert f["discarded_minutes"] == 3590 and f["discarded_days"] == 2 and f["warmup_bars"] == 3000
    assert f["stops_level_pts"] == 20 and f["every_tick_generation_used"]
    assert journal.redact("'4416506': authorized from 85.64.1.2 at 4430.21") == "'<acct>': authorized from <ip> at 4430.21"
