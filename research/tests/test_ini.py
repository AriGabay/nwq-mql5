import pytest

from mt5r import ini


def test_m15_real_ticks_ini():
    text = ini.render(expert="new_test.ex5", symbol="XAUUSD.s", period="M15", from_date="2026.03.01",
                      to_date_inclusive="2026.03.31", deposit=10000, report="reports\\x", set_lines=["PivL=3||3||0||3||N"])
    lines = text.split("\r\n")
    for want in ["Period=M15", "Model=4", "Deposit=10000", "Currency=USD", "Leverage=1:100", "Visual=0",
                 "ShutdownTerminal=1", "ToDate=2026.04.01", "FromDate=2026.03.01", "ProfitInPips=0",
                 "[TesterInputs]", "PivL=3||3||0||3||N"]:
        assert want in lines, want


def test_deposit_keeps_cents():
    text = ini.render(expert="e.ex5", symbol="S", period="M15", from_date="2026.03.01", to_date_inclusive="2026.03.31",
                      deposit=10123.45, report="r", set_lines=[])
    assert "Deposit=10123.45" in text.split("\r\n")


def test_unknown_period_rejected():
    with pytest.raises(ValueError):
        ini.render(expert="e", symbol="S", period="M7", from_date="a", to_date_inclusive="2026.01.01",
                   deposit=1, report="r", set_lines=[])
