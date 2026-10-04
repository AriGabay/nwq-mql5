"""Parse MT5 tester reports produced by the isolated (English-language) terminal.

- single test: HTML (UTF-16) -> inputs, summary metrics, deals table
- optimization: SpreadsheetML XML -> one row per pass
- research CSVs written by the research build (rl_days_*, rl_deals_*, rl_frames_*)
"""
import html
import pathlib
import re
import xml.etree.ElementTree as ET

import pandas as pd

from .textio import read_text

ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")


def _read(path) -> str:
    return read_text(path, errors="replace")


def _cells(row_html: str) -> list:
    return [html.unescape(TAG_RE.sub("", c)).strip() for c in CELL_RE.findall(row_html)]


def _num(s: str) -> float:
    s = s.replace("\xa0", " ").replace(" ", "").split("(")[0].rstrip(")").rstrip("%")
    return float(s) if s not in ("", "-") else float("nan")


def parse_html(path) -> dict:
    """Return {'header', 'inputs', 'metrics', 'deals'} from a single-test report."""
    text = _read(path)
    rows = [_cells(r) for r in ROW_RE.findall(text)]
    header, inputs, metrics = {}, {}, {}
    in_inputs = False
    deals_at = None
    for i, cells in enumerate(rows):
        if cells and cells[0] == "Deals" and len(cells) == 1:
            deals_at = i
            break
        if not cells:
            continue
        if cells[0] == "Inputs:":
            in_inputs = True
        elif cells[0].endswith(":") and cells[0] != "":
            in_inputs = False
        if in_inputs and len(cells) >= 2 and "=" in cells[1] and not cells[1].startswith("==="):
            k, v = cells[1].split("=", 1)
            inputs[k] = v
            continue
        # label/value pairs across the row
        for j in range(0, len(cells) - 1):
            label = cells[j]
            if label.endswith(":") and cells[j + 1] and not cells[j + 1].endswith(":"):
                key = label[:-1]
                if key in ("Expert", "Symbol", "Period", "Company", "Currency", "Initial Deposit", "Leverage",
                           "History Quality", "Bars", "Ticks"):
                    header[key] = cells[j + 1]
                else:
                    metrics[key] = cells[j + 1]
    deals = _deals(rows[deals_at + 1:]) if deals_at is not None else pd.DataFrame()
    build = re.search(r"\(Build (\d+)\)", text)
    header["Build"] = build.group(1) if build else None
    return {"header": header, "inputs": inputs, "metrics": metrics, "deals": deals}


def _deals(rows) -> pd.DataFrame:
    cols = rows[0]
    out = []
    for cells in rows[1:]:
        if len(cells) != len(cols) or not re.match(r"\d{4}\.\d{2}\.\d{2}", cells[0]):
            continue
        out.append(dict(zip(cols, cells)))
    df = pd.DataFrame(out, columns=cols)
    if df.empty:
        return df
    df["Time"] = pd.to_datetime(df["Time"], format="%Y.%m.%d %H:%M:%S")
    for c in ["Volume", "Price", "Commission", "Swap", "Profit", "Balance"]:
        df[c] = df[c].map(lambda s: _num(s) if s else float("nan"))
    return df


def summary(report: dict) -> dict:
    """Headline numbers from a parsed single-test report."""
    m = report["metrics"]
    eq_dd = m.get("Equity Drawdown Maximal", "0 (0%)")
    return {
        "net_profit": _num(m["Total Net Profit"]),
        "trades": int(_num(m["Total Trades"])),
        "profit_factor": _num(m["Profit Factor"]),
        "recovery_factor": _num(m["Recovery Factor"]),
        "equity_dd_money": _num(eq_dd),
        "equity_dd_pct": _num(eq_dd.split("(")[1]) if "(" in eq_dd else float("nan"),
        "balance_dd_pct": _num(m.get("Balance Drawdown Maximal", "0 (0%)").split("(")[1])
        if "(" in m.get("Balance Drawdown Maximal", "") else float("nan"),
    }


# ---------------------------------------------------------------- optimization XML
SS = "{urn:schemas-microsoft-com:office:spreadsheet}"
XML_COLS = {"Pass": "pass", "Result": "result", "Profit": "profit", "Expected Payoff": "expected_payoff",
            "Profit Factor": "profit_factor", "Recovery Factor": "recovery_factor", "Sharpe Ratio": "sharpe",
            "Custom": "custom", "Equity DD %": "eq_dd_pct", "Trades": "trades"}


def parse_opt_xml(path) -> pd.DataFrame:
    """One row per pass. Required columns fail loudly when absent (KTD6)."""
    root = ET.fromstring(pathlib.Path(path).read_bytes())
    table = root.find(f".//{SS}Table")
    rows = []
    for r in table.findall(f"{SS}Row"):
        rows.append([(c.find(f"{SS}Data").text if c.find(f"{SS}Data") is not None else None)
                     for c in r.findall(f"{SS}Cell")])
    head, body = rows[0], rows[1:]
    df = pd.DataFrame(body, columns=head)
    missing = [c for c in XML_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"optimization XML lacks columns {missing}")
    df = df.rename(columns=XML_COLS)
    for c in df.columns:
        try:   # pandas 3 dropped to_numeric(errors="ignore"): a non-numeric column stays as text
            df[c] = pd.to_numeric(df[c])
        except (ValueError, TypeError):
            pass
    return df


# ---------------------------------------------------------------- research CSVs
def read_days(path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"], format="%Y.%m.%d")
    return df


def read_deals(path) -> pd.DataFrame:
    df = pd.read_csv(path, keep_default_na=False)
    df["time"] = pd.to_datetime(df["time"], format="%Y.%m.%d %H:%M:%S")
    return df


def trades_from_deals(deals: pd.DataFrame, initial: float) -> pd.DataFrame:
    """Closed positions from research deal records, with balance at entry (running closed balance).

    type: 0 buy, 1 sell, 2 balance; entry: 0 in, 1 out, 2 inout, 3 out_by.
    """
    deals = deals.sort_values(["time", "ticket"]).reset_index(drop=True)
    balance = initial
    open_pos, out = {}, []
    for d in deals.itertuples(index=False):
        if d.type == 2:
            continue
        if d.entry == 0:
            open_pos[d.position_id] = {"open_time": d.time, "direction": 1 if d.type == 0 else -1,
                                       "volume": d.volume, "open_price": d.price, "commission": d.commission,
                                       "swap": d.swap, "profit": d.profit, "balance_at_open": balance,
                                       "comment": d.comment}
            balance += d.commission + d.swap + d.profit
        else:
            p = open_pos.get(d.position_id)
            balance += d.commission + d.swap + d.profit
            if p is None:
                continue
            p["commission"] += d.commission
            p["swap"] += d.swap
            p["profit"] += d.profit
            p["close_time"] = d.time
            p["close_price"] = d.price
            p["closed_volume"] = p.get("closed_volume", 0.0) + d.volume
            if p["closed_volume"] >= p["volume"] - 1e-9:
                out.append(open_pos.pop(d.position_id))
    cols = ["open_time", "close_time", "direction", "volume", "open_price", "close_price", "profit",
            "commission", "swap", "balance_at_open", "comment"]
    return pd.DataFrame(out, columns=cols).sort_values("open_time", kind="mergesort").reset_index(drop=True)


def read_frames(path) -> tuple:
    """(passes, days) from rl_frames_*.csv: passes has params + cost totals; days has per-pass day rows."""
    passes, days = [], []
    for line in pathlib.Path(path).read_text(errors="replace").splitlines()[1:]:
        f = line.split(",")
        if f[0] == "P":
            params = dict(kv.split("=", 1) for kv in f[7].split(";") if "=" in kv) if len(f) > 7 else {}
            passes.append({"pass": int(f[1]), "daily_sharpe": float(f[2]), "commission": float(f[3]),
                           "swap": float(f[4]), "lots": float(f[5]), "entries": int(float(f[6])), **params})
        elif f[0] == "D":
            days.append({"pass": int(f[1]), "date": f[2], "bal_open": float(f[3]), "eq_open": float(f[4]),
                         "eq_min": float(f[5]), "eq_max": float(f[6]), "bal_close": float(f[7]),
                         "eq_close": float(f[8]), "spread_median": float(f[9])})
    d = pd.DataFrame(days)
    if not d.empty:
        d["date"] = pd.to_datetime(d["date"], format="%Y.%m.%d")
    return pd.DataFrame(passes), d
