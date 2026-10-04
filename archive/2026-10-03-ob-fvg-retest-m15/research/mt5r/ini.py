"""Tester ini rendering (documented [Tester] keys, UTF-16LE)."""
import datetime as dt

MODEL_REAL_TICKS = 4
PERIODS = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 16385}


def next_day(date_str: str) -> str:
    """Tester ToDate is exclusive of that day: a window ending on D uses D+1."""
    d = dt.datetime.strptime(date_str, "%Y.%m.%d").date() + dt.timedelta(days=1)
    return d.strftime("%Y.%m.%d")


def render(*, expert: str, symbol: str, period: str, from_date: str, to_date_inclusive: str,
           deposit: float, report: str, set_lines: list, leverage: str = "1:100", currency: str = "USD",
           model: int = MODEL_REAL_TICKS, optimization: int = 0, criterion: int = 6,
           execution_mode: int = 0) -> str:
    """Return ini text. `set_lines` are [TesterInputs] lines as produced by setfile.render_lines()."""
    if period not in PERIODS:
        raise ValueError(f"unsupported period {period}")
    lines = [
        "[Tester]",
        f"Expert={expert}",
        f"Symbol={symbol}",
        f"Period={period}",
        f"Model={model}",
        f"ExecutionMode={execution_mode}",
        f"Optimization={optimization}",
        f"OptimizationCriterion={criterion}",
        f"FromDate={from_date}",
        f"ToDate={next_day(to_date_inclusive)}",
        "ForwardMode=0",
        f"Deposit={deposit:.2f}".rstrip("0").rstrip("."),
        f"Currency={currency}",
        f"Leverage={leverage}",
        "ProfitInPips=0",
        "UseLocal=1",
        "UseRemote=0",
        "UseCloud=0",
        "Visual=0",
        f"Report={report}",
        "ReplaceReport=1",
        "ShutdownTerminal=1",
        "[TesterInputs]",
        *set_lines,
    ]
    return "\r\n".join(lines) + "\r\n"
