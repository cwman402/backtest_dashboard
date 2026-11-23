#!/usr/bin/env python3
"""
bt.py - command-line backtest blender for the Backtest Dashboard.

Loads one or more CSV exports, maps their columns the same way index.html does,
combines them into a weighted portfolio (rebalanced each period) and prints
performance stats, plus an equity / drawdown / rolling-Sharpe / monthly-return
report as PNG, JPG, SVG and PDF (all written by default).

Blend weights are manual/equal by default; --weighting also offers inverse
volatility (riskparity), inverse max drawdown, Sharpe and Calmar weighting,
mirroring the weighting options in index.html.

Examples
--------
  ./bt.py sample.csv
  ./bt.py a.csv b.csv -w 60,40
  ./bt.py a.csv b.csv -w a=70 -w b=30 --rf 2 --ppy 252 -f both
  ./bt.py a.csv b.csv --weighting riskparity
  ./bt.py *.csv --start 2022-01-01 --end 2023-12-31 -o out/q4.png
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning)

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Rectangle

# --------------------------------------------------------------------------
# Theme (matches index.html)
# --------------------------------------------------------------------------
BG = "#0b0e14"
PANEL = "#131722"
LINE = "#242b3d"
TEXT = "#e7ecf5"
MUTED = "#8b96ad"
GREEN = "#22c98a"
RED = "#ff5c6c"
WHITE = "#ffffff"
PALETTE = [
    "#4f9cff", "#ff6b6b", "#2ec4b6", "#f7b801", "#b388ff",
    "#ff9f43", "#00d4aa", "#e84a5f", "#7bd389", "#f06595",
]

# --------------------------------------------------------------------------
# Column auto-detection (mirrors index.html)
# --------------------------------------------------------------------------
RX = {
    "date": re.compile(r"date|datetime|timestamp|time|period|week|month|day|dt$"),
    "benchmark": re.compile(r"benchmark|buyhold|buyandhold|bh$|spx|spy|^index|market$"),
    "tradePnl": re.compile(r"trade.*pnl|^pnl|pnl$|profit|realized|pl$"),
    "returns": re.compile(r"return|ret$|ret_|dailyret|pct|percent|pnlpct|change"),
    "equity": re.compile(r"equity|nav|portfolio|balance|accountvalue|value|capital|networth|cum|wealth|curve"),
}


def norm_key(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).strip().lower())


def parse_number(v) -> float:
    """Lenient numeric parse: $, %, thousands separators, (negatives)."""
    if v is None:
        return float("nan")
    if isinstance(v, (int, float, np.integer, np.floating)):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return float("nan")
        return f if math.isfinite(f) else float("nan")
    s = str(v).strip()
    if not s:
        return float("nan")
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    s = re.sub(r"[%$£€,\s]", "", s)
    if s in ("", "-", "—", "n/a", "N/A", "NA", "null", "None"):
        return float("nan")
    try:
        f = float(s)
    except ValueError:
        return float("nan")
    if not math.isfinite(f):
        return float("nan")
    return -f if neg else f


def numeric_fraction(vals) -> float:
    vals = [v for v in vals if str(v).strip() != ""]
    if not vals:
        return 0.0
    return sum(1 for v in vals if not math.isnan(parse_number(v))) / len(vals)


def parse_dates(raw) -> pd.Series | None:
    """Parse a date column, choosing month-first vs day-first by how well it fits.

    Backtest exports are chronological, so the parse that keeps the file order
    increasing wins (and month-first is preferred on a tie, like index.html).
    """
    s = pd.Series(raw).astype(str).str.strip()
    best = None
    for dayfirst in (False, True):
        try:
            cand = pd.to_datetime(s, errors="coerce", format="mixed", dayfirst=dayfirst)
        except (ValueError, TypeError):
            continue
        ok = float(cand.notna().mean())
        vals = cand.dropna()
        mono = float((vals.diff().dropna() >= pd.Timedelta(0)).mean()) if len(vals) > 1 else 1.0
        score = (ok, mono)
        if best is None or score > best[0]:
            best = (score, cand)
    return None if best is None else best[1]


def date_fraction(vals) -> float:
    parsed = parse_dates(vals)
    if parsed is None:
        return 0.0
    return float(parsed.notna().mean())



def sample_column(df: pd.DataFrame, col: str, n: int = 12):
    out = []
    for v in df[col].tolist():
        if str(v).strip() != "":
            out.append(v)
            if len(out) >= n:
                break
    return out


def detect_mapping(header, df) -> dict:
    """Pick date/equity/returns/tradePnl/benchmark columns like the web app."""
    numeric = {h: numeric_fraction(sample_column(df, h)) for h in header}

    # date: prefer a name that looks temporal and isn't numeric, else by values
    date_col = None
    for h in header:
        if RX["date"].search(norm_key(h)) and numeric[h] < 0.5 and date_fraction(sample_column(df, h)) >= 0.6:
            date_col = h
            break
    if date_col is None:
        for h in header:
            if date_fraction(sample_column(df, h)) >= 0.8:
                date_col = h
                break
    if date_col is None and header:
        date_col = header[0]

    num_cols = [h for h in header if h != date_col and numeric[h] >= 0.6]

    def pick(rx, exclude=()):
        for h in num_cols:
            if h in exclude:
                continue
            if rx.search(norm_key(h)):
                return h
        return None

    mapping = {"date": date_col, "equity": None, "returns": None, "tradePnl": None, "benchmark": None}
    mapping["benchmark"] = pick(RX["benchmark"])

    ret_cols = [h for h in num_cols if RX["returns"].search(norm_key(h)) and h != mapping["benchmark"]]
    ret_cols.sort(key=lambda h: 0 if "strategy" in norm_key(h) else 1)
    mapping["returns"] = ret_cols[0] if ret_cols else None

    mapping["tradePnl"] = pick(RX["tradePnl"], exclude=(mapping["benchmark"], mapping["returns"]))
    mapping["equity"] = pick(
        RX["equity"], exclude=(mapping["benchmark"], mapping["returns"], mapping["tradePnl"])
    )

    if not mapping["equity"] and not mapping["returns"] and not mapping["tradePnl"] and num_cols:
        mapping["equity"] = num_cols[0]
    return mapping


# --------------------------------------------------------------------------
# Dataset
# --------------------------------------------------------------------------
class Dataset:
    def __init__(self, path, name, df, header, mapping, color):
        self.path = path
        self.name = name
        self.df = df
        self.header = header
        self.mapping = mapping
        self.color = color
        self.weight = 0.0
        self.curve = pd.Series(dtype=float)      # equity level, date index
        self.benchmark = None                     # rebased benchmark level
        self.trades = None                        # list of trade p&l values
        self.source = "equity"                    # how the curve was built
        self.build()

    def build(self):
        df, m = self.df, self.mapping
        n = len(df)
        if n == 0:
            return

        # --- dates first: everything below depends on the row order -------------
        dates = None
        if m["date"]:
            parsed = parse_dates(df[m["date"]].astype(str))
            if parsed is not None and parsed.notna().mean() >= 0.8:
                keep = parsed.notna().to_numpy()
                df = df.loc[keep].reset_index(drop=True)
                parsed = parsed.loc[keep].reset_index(drop=True)
                # chronological, one row per timestamp (later rows win)
                order = np.argsort(parsed.to_numpy(), kind="stable")
                df = df.iloc[order].reset_index(drop=True)
                parsed = parsed.iloc[order].reset_index(drop=True)
                dup = parsed.duplicated(keep="last").to_numpy()
                df = df.loc[~dup].reset_index(drop=True)
                parsed = parsed.loc[~dup].reset_index(drop=True)
                dates = pd.DatetimeIndex(parsed).normalize()
                n = len(df)

        def col(name):
            return df[name].map(parse_number).astype(float).to_numpy()

        ret_vals = None
        if m["returns"]:
            ret_vals = col(m["returns"])
            raw = df[m["returns"]].astype(str)
            pct_strings = raw.str.contains("%").mean() >= 0.5
            finite = ret_vals[np.isfinite(ret_vals)]
            max_abs = float(np.max(np.abs(finite))) if finite.size else 0.0
            if pct_strings or max_abs > 1.5:
                ret_vals = ret_vals / 100.0

        eq_vals = col(m["equity"]) if m["equity"] else None
        if eq_vals is not None:
            finite = eq_vals[np.isfinite(eq_vals)]
            if finite.size and (finite <= 0).mean() > 0.15:
                # a P&L / return stream masquerading as an equity curve
                if not m["tradePnl"]:
                    m["tradePnl"] = m["equity"]
                    self.mapping["tradePnl"] = m["equity"]
                eq_vals, m["equity"] = None, None

        pl_vals = col(m["tradePnl"]) if m["tradePnl"] else None

        if eq_vals is not None:
            equity = eq_vals
            self.source = "equity"
        elif ret_vals is not None:
            acc, equity = 100.0, []
            for r in ret_vals:
                if np.isfinite(r):
                    acc *= 1.0 + r
                equity.append(acc)
            equity = np.asarray(equity, dtype=float)
            self.source = "returns"
        elif pl_vals is not None:
            clean = np.where(np.isfinite(pl_vals), pl_vals, 0.0)
            # keep the curve positive even if cumulative P&L dips below zero
            start = 100.0 + max(0.0, -float(np.cumsum(clean).min()) + 1.0)
            acc, equity = start, []
            for p in pl_vals:
                if np.isfinite(p):
                    acc += p
                equity.append(acc)
            equity = np.asarray(equity, dtype=float)
            self.source = "trades"
        else:
            equity = np.full(n, 100.0)
            self.source = "none"

        idx = pd.RangeIndex(n) if dates is None else dates

        curve = pd.Series(equity, index=idx, name=self.name).astype(float)
        good = np.isfinite(curve.to_numpy()) & (curve.to_numpy() > 0)
        curve = curve[good]
        self.curve = curve

        if m["tradePnl"] and pl_vals is not None:
            vals = pd.Series(pl_vals, index=idx)[good]
            vals = vals[np.isfinite(vals.to_numpy())].to_numpy()
            self.trades = vals if vals.size > 1 else None

        if m["benchmark"] and len(curve):
            bm = pd.Series(col(m["benchmark"]), index=idx)[good]
            bm = bm[np.isfinite(bm.to_numpy()) & (bm.to_numpy() > 0)]
            if len(bm) > 1:
                self.benchmark = bm / bm.iloc[0] * float(curve.iloc[0])
        self.n_raw = n

    def describe(self):
        bits = []
        for key in ("date", "equity", "returns", "tradePnl", "benchmark"):
            if self.mapping.get(key):
                bits.append(f"{key}={self.mapping[key]}")
        return ", ".join(bits)


# --------------------------------------------------------------------------
# Analytics (mirrors computeStats in index.html)
# --------------------------------------------------------------------------
def stdev(a, mu=None):
    a = np.asarray(a, dtype=float)
    if a.size < 2:
        return float("nan")
    if mu is None:
        mu = a.mean()
    return float(np.sqrt(((a - mu) ** 2).sum() / (a.size - 1)))


def _clean_curve(curve: pd.Series) -> np.ndarray:
    clean = curve.dropna()
    clean = clean[np.isfinite(clean.to_numpy(dtype=float)) & (clean.to_numpy(dtype=float) > 0)]
    return clean.to_numpy(dtype=float)


def annual_vol(curve: pd.Series, ppy: int) -> float:
    """Annualised stdev of period returns, matching compute_stats' annVol."""
    e = _clean_curve(curve)
    if e.size < 3:
        return float("nan")
    return stdev(e[1:] / e[:-1] - 1.0) * math.sqrt(ppy)


def max_drawdown(curve: pd.Series) -> float:
    """Deepest peak-to-trough loss (<= 0), matching compute_stats' maxdd."""
    e = _clean_curve(curve)
    if e.size < 2:
        return float("nan")
    peak, mdd = e[0], 0.0
    for v in e:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)
    return float(mdd)


def sharpe_ratio(curve: pd.Series, ppy: int, rf: float) -> float:
    """Annualised Sharpe ratio, matching compute_stats' sharpe."""
    e = _clean_curve(curve)
    if e.size < 3:
        return float("nan")
    r = e[1:] / e[:-1] - 1.0
    sd = stdev(r)
    if not (sd > 0):
        return float("nan")
    return (float(r.mean()) * ppy - rf) / (sd * math.sqrt(ppy))


def calmar_ratio(curve: pd.Series, ppy: int) -> float:
    """CAGR / |max drawdown|, matching compute_stats' calmar."""
    e = _clean_curve(curve)
    if e.size < 2:
        return float("nan")
    years = (e.size - 1) / ppy
    if not (years > 0):
        return float("nan")
    mdd = max_drawdown(curve)
    if not (mdd < 0):
        return float("nan")
    cagr = (e[-1] / e[0]) ** (1.0 / years) - 1.0
    return float(cagr / abs(mdd))


def compute_stats(equity: pd.Series, ppy: int, rf: float, roll: int, trades=None) -> dict:
    clean = equity.dropna()
    clean = clean[np.isfinite(clean.to_numpy(dtype=float)) & (clean.to_numpy(dtype=float) > 0)]
    e = clean.to_numpy(dtype=float)
    e_idx = clean.index
    out = {"n": int(e.size), "valid": False, "equity": equity, "tradeSource": "periods"}
    if e.size < 2:
        return out

    out["valid"] = True
    r = e[1:] / e[:-1] - 1.0
    m = r.size
    mu, sd = float(r.mean()), stdev(r)

    out["totalReturn"] = float(e[-1] / e[0] - 1.0)
    years = m / ppy
    out["years"] = years
    out["cagr"] = float((e[-1] / e[0]) ** (1.0 / years) - 1.0) if years > 0 else float("nan")
    out["annVol"] = sd * math.sqrt(ppy)
    out["sharpe"] = (mu * ppy - rf) / out["annVol"] if out["annVol"] > 0 else float("nan")

    downside = math.sqrt(float(np.mean(np.minimum(r, 0.0) ** 2)))
    out["sortino"] = (mu * ppy - rf) / (downside * math.sqrt(ppy)) if downside > 0 else float("nan")

    peak, maxdd, uw = e[0], 0.0, []
    for v in e:
        peak = max(peak, v)
        dd = v / peak - 1.0
        uw.append(dd)
        maxdd = min(maxdd, dd)
    out["maxdd"] = maxdd
    out["underwater"] = np.asarray(uw)
    out["calmar"] = out["cagr"] / abs(maxdd) if maxdd < 0 else float("nan")

    out["best"] = float(r.max())
    out["worst"] = float(r.min())
    var95 = -float(np.percentile(r, 5))
    tail = r[r <= np.percentile(r, 5)]
    out["var95"] = var95
    out["cvar95"] = -float(tail.mean()) if tail.size else float("nan")
    out["skew"] = float(np.mean(((r - mu) / sd) ** 3)) if sd > 0 else float("nan")
    out["kurt"] = float(np.mean(((r - mu) / sd) ** 4) - 3.0) if sd > 0 else float("nan")
    out["avg"] = mu
    out["returns"] = r

    w = int(min(roll, max(2, m - 1)))
    roll_y, roll_x = [], []
    for i in range(w, m + 1):
        seg = r[i - w: i]
        s_mu, s_sd = float(seg.mean()), stdev(seg)
        roll_y.append((s_mu * ppy - rf) / (s_sd * math.sqrt(ppy)) if s_sd > 0 else float("nan"))
        roll_x.append(e_idx[i] if i < len(e_idx) else i)
    out["rollW"] = w
    out["rollX"] = roll_x
    out["rollY"] = np.asarray(roll_y, dtype=float)

    if trades is not None and len(trades) > 1:
        vals = np.asarray(trades, dtype=float)
        vals = vals[np.isfinite(vals)]
        out["tradeSource"] = "trades"
    else:
        vals = r
    wins, losses = vals[vals > 0], vals[vals < 0]
    out["nTrades"] = int(vals.size)
    out["winRate"] = wins.size / vals.size if vals.size else float("nan")
    gp, gl = float(wins.sum()), float(abs(losses.sum()))
    out["profitFactor"] = gp / gl if gl > 0 else (float("inf") if gp > 0 else float("nan"))
    out["avgWin"] = float(wins.mean()) if wins.size else float("nan")
    out["avgLoss"] = float(losses.mean()) if losses.size else float("nan")
    out["payoff"] = abs(out["avgWin"] / out["avgLoss"]) if math.isfinite(out["avgLoss"]) and out["avgLoss"] else float("nan")
    out["expectancy"] = out["winRate"] * out["avgWin"] + (1 - out["winRate"]) * out["avgLoss"]
    out["trades"] = vals

    cw = cl = mw = ml = 0
    for v in vals:
        if v > 0:
            cw, cl, mw = cw + 1, 0, max(mw, cw + 1)
        elif v < 0:
            cl, cw, ml = cl + 1, 0, max(ml, cl + 1)
        else:
            cw = cl = 0
    out["maxWinStreak"] = max(mw, 0)
    out["maxLossStreak"] = max(ml, 0)
    return out


def monthly_table(equity: pd.Series):
    """year -> np.array(12) of monthly compounded returns (None for gaps)."""
    if not isinstance(equity.index, pd.DatetimeIndex) or len(equity) < 2:
        return None
    e = equity.dropna()
    df = pd.DataFrame({"e": e})
    df["r"] = df["e"].pct_change(fill_method=None)
    df = df.iloc[1:]
    if df.empty:
        return None
    grp = df.groupby([df.index.year, df.index.month])["r"].apply(lambda s: (1 + s).prod() - 1)
    table = {}
    for (y, mo), val in grp.items():
        table.setdefault(int(y), np.full(12, np.nan))[int(mo) - 1] = float(val)
    return table


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
def load_csv(path: str) -> tuple[pd.DataFrame, list[str]]:
    last_err = None
    for kwargs in (
        dict(sep=None, engine="python"),
        dict(sep=","),
        dict(sep=";"),
    ):
        try:
            df = pd.read_csv(
                path, dtype=str, keep_default_na=False, encoding="utf-8-sig", **kwargs
            )
            break
        except Exception as err:  # noqa: BLE001
            last_err = err
            df = None
    if df is None:
        raise SystemExit(f"bt.py: could not read {path}: {last_err}")

    df = df.dropna(axis=1, how="all")
    # drop columns that hold nothing but blanks (e.g. a trailing comma)
    if len(df.columns):
        empty_col = (df.apply(lambda c: c.astype(str).str.strip() == "")).all()
        df = df.loc[:, ~empty_col]
    if len(df.columns) == 0:
        raise SystemExit(f"bt.py: {path} has no usable columns")
    cols = []
    for i, c in enumerate(df.columns):
        name = str(c).strip()
        if not name or name.startswith("Unnamed:"):
            name = f"col{i}"
        cols.append(name)
    df.columns = cols

    # drop rows that are entirely blank
    blank = (df.apply(lambda c: c.astype(str).str.strip() == "")).all(axis=1)
    df = df[~blank].reset_index(drop=True)
    if df.empty or len(df.columns) == 0:
        raise SystemExit(f"bt.py: {path} has no usable rows")
    return df, cols


def build_datasets(paths):
    datasets = []
    used = set()
    for i, path in enumerate(paths):
        if not os.path.exists(path):
            raise SystemExit(f"bt.py: file not found: {path}")
        df, header = load_csv(path)
        stem = os.path.splitext(os.path.basename(path))[0]
        name = stem if stem not in used else f"{stem}_{i + 1}"
        used.add(name)
        mapping = detect_mapping(header, df)
        color = PALETTE[len(datasets) % len(PALETTE)]
        ds = Dataset(path, name, df, header, mapping, color)
        if len(ds.curve) < 2:
            print(f"bt.py: warning: {path} yielded fewer than 2 usable rows - skipped", file=sys.stderr)
            continue
        datasets.append(ds)
    if not datasets:
        raise SystemExit("bt.py: no usable datasets loaded")
    return datasets


def apply_weights(datasets, specs):
    n = len(datasets)
    weights = [None] * n
    names = {d.name.lower(): i for i, d in enumerate(datasets)}
    names.update({os.path.basename(d.path).lower(): i for i, d in enumerate(datasets)})
    positional = []

    for spec in specs:
        for part in str(spec).replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            if "=" in part or ":" in part:
                key, _, val = part.partition("=") if "=" in part else part.partition(":")
                idx = names.get(key.strip().lower())
                if idx is None:
                    raise SystemExit(
                        f"bt.py: unknown dataset '{key.strip()}'. Known: {', '.join(d.name for d in datasets)}"
                    )
                weights[idx] = parse_number(val)
            else:
                positional.append(parse_number(part))

    if positional:
        if len(positional) > n:
            raise SystemExit(f"bt.py: {len(positional)} weights given for {n} datasets")
        for i, w in enumerate(positional):
            if weights[i] is None:
                weights[i] = w

    missing = [i for i, w in enumerate(weights) if w is None]
    if missing and any(w is not None for w in weights):
        for i in missing:
            weights[i] = 0.0
    elif missing:
        weights = [1.0] * n

    for i, w in enumerate(weights):
        if w is None or not math.isfinite(w) or w < 0:
            raise SystemExit(f"bt.py: weight for {datasets[i].name} must be a non-negative number")
    if sum(weights) <= 0:
        raise SystemExit("bt.py: weights must not all be zero")

    for ds, w in zip(datasets, weights):
        ds.weight = float(w)
    return weights


DD_FLOOR = 1e-4  # 0.01%: keeps a drawdown-free curve's inverse weight finite


def apply_weighting(datasets, mode: str, ppy: int, rf: float = 0.0):
    """Set blend weights for the chosen scheme (mirrors index.html).

    manual      keep whatever -w / apply_weights resolved (equal when omitted)
    equal       1/N across every loaded dataset
    riskparity  inverse volatility, w_i ∝ 1/σ_i, normalised to 1, with σ the
                annualised stdev of period returns over the loaded date range
    drawdown    inverse max drawdown, w_i ∝ 1/|maxDD_i| (deeper losses get a
                smaller share; a 0% drawdown is floored at 0.01%)
    sharpe      w_i ∝ Sharpe_i over the loaded sample (rf and ppy aware)
    calmar      w_i ∝ Calmar_i = CAGR / |maxDD|

    Ratio schemes ignore non-positive/undefined values; any scheme falls back
    to equal weights when no dataset has a usable estimate, and datasets with a
    zero share sit out of the blend.
    """
    if mode == "manual":
        return
    if mode == "equal":
        for d in datasets:
            d.weight = 1.0
        return

    raw = {}
    for d in datasets:
        if mode == "riskparity":
            v = annual_vol(d.curve, ppy)
            raw[d.name] = 1.0 / v if math.isfinite(v) and v > 0 else float("nan")
        elif mode == "drawdown":
            v = max_drawdown(d.curve)
            raw[d.name] = 1.0 / max(abs(v), DD_FLOOR) if math.isfinite(v) else float("nan")
        elif mode == "sharpe":
            v = sharpe_ratio(d.curve, ppy, rf)
            raw[d.name] = v if math.isfinite(v) and v > 0 else float("nan")
        else:  # calmar
            v = calmar_ratio(d.curve, ppy)
            raw[d.name] = v if math.isfinite(v) and v > 0 else float("nan")

    usable = {name: v for name, v in raw.items() if math.isfinite(v) and v > 0}
    if not usable:
        print(
            f"bt.py: warning: no usable risk/return estimates for --weighting {mode}"
            " - using equal weights",
            file=sys.stderr,
        )
        for d in datasets:
            d.weight = 1.0
        return
    total = sum(usable.values())
    for d in datasets:
        d.weight = usable.get(d.name, 0.0) / total


# --------------------------------------------------------------------------
# Blending
# --------------------------------------------------------------------------
def align(datasets):
    """Return (grid, {name: equity-on-grid}) with a shared index."""
    dated = [isinstance(d.curve.index, pd.DatetimeIndex) for d in datasets]
    if all(dated):
        grid = sorted(set().union(*[set(d.curve.index) for d in datasets]))
        grid = pd.DatetimeIndex(grid)
    else:
        if any(dated) and not all(dated):
            print("bt.py: warning: mixing dated and undated files - aligning by row number", file=sys.stderr)
        grid = pd.RangeIndex(max(len(d.curve) for d in datasets))

    aligned = {}
    for d in datasets:
        c = d.curve
        if isinstance(grid, pd.DatetimeIndex) and isinstance(c.index, pd.DatetimeIndex):
            aligned[d.name] = c.reindex(grid).ffill()
        else:
            vals = c.to_numpy(dtype=float)
            padded = np.full(len(grid), np.nan)
            padded[: min(len(vals), len(grid))] = vals[: len(grid)]
            aligned[d.name] = pd.Series(padded, index=grid)
    return grid, aligned


def blend(datasets, mode="rebalanced", base=100.0, strict=False):
    active = [d for d in datasets if d.weight > 0]
    if not active:
        raise SystemExit("bt.py: no dataset has a positive weight")
    grid, aligned = align(active)
    frame = pd.DataFrame(aligned)
    weights = pd.Series({d.name: d.weight for d in active}, dtype=float)
    weights = weights / weights.sum()

    if mode == "buyhold":
        norm = frame.apply(lambda s: s / s.dropna().iloc[0] if s.notna().any() else s)
        avail = norm.notna()
    else:
        norm = frame.pct_change(fill_method=None)
        avail = frame.notna()
        avail.iloc[0] = False

    wmat = pd.DataFrame(
        np.tile(weights.to_numpy(), (len(grid), 1)), index=grid, columns=frame.columns
    ).where(avail)
    wsum = wmat.sum(axis=1).replace(0.0, np.nan)

    if mode == "buyhold":
        total = (norm.fillna(0.0) * wmat).sum(axis=1) / wsum
        equity = base * total
    else:
        r = (norm.fillna(0.0) * wmat).sum(axis=1) / wsum
        r = r.fillna(0.0)
        equity = base * (1.0 + r).cumprod()

    coverage = wmat.sum(axis=1) / weights.sum()
    coverage.iloc[0] = np.nan
    equity = equity[equity > 0]

    if strict:
        live = coverage.reindex(equity.index) >= 0.999
        if live.any():
            equity = equity.loc[live[live].index[0]:]
            coverage = coverage.reindex(equity.index)
    return equity, frame, coverage.dropna()


# --------------------------------------------------------------------------
# Terminal output
# --------------------------------------------------------------------------
def _pct(x, d=2, sign=False):
    if x is None or not isinstance(x, (int, float, np.floating, np.integer)) or not math.isfinite(x):
        return "—"
    s = f"{x * 100:+.{d}f}%" if sign else f"{x * 100:.{d}f}%"
    return s


def _num(x, d=2):
    if x is None or not math.isfinite(x):
        return "∞" if x == float("inf") else "—"
    return f"{x:.{d}f}"


def print_report(datasets, blend_stats, settings, coverage, out_paths):
    show_blend = len(datasets) > 1
    cols = [d.name for d in datasets] + (["BLEND"] if show_blend else [])

    def g(d, key):
        return d.stats.get(key, float("nan"))

    def both(fn, key):
        vals = [fn(g(d, key)) for d in datasets]
        return vals + ([fn(blend_stats[key])] if show_blend else [])

    rows = [
        ("Total return", both(lambda v: _pct(v, sign=True), "totalReturn")),
        ("CAGR", both(lambda v: _pct(v, sign=True), "cagr")),
        ("Volatility", both(_pct, "annVol")),
        ("Sharpe", both(_num, "sharpe")),
        ("Sortino", both(_num, "sortino")),
        ("Calmar", both(_num, "calmar")),
        ("Max drawdown", both(_pct, "maxdd")),
        ("Best period", both(lambda v: _pct(v, sign=True), "best")),
        ("Worst period", both(lambda v: _pct(v, sign=True), "worst")),
        ("Win rate", both(lambda v: _pct(v, 1), "winRate")),
        ("Profit factor", both(_num, "profitFactor")),
        ("Expectancy", both(lambda v: _pct(v, 3, True), "expectancy")),
        ("Payoff", both(_num, "payoff")),
        ("VaR 95%", both(_pct, "var95")),
        ("CVaR 95%", both(_pct, "cvar95")),
        ("Skew", both(_num, "skew")),
        ("Periods", both(lambda v: str(v), "n")),
    ]

    label_w = max(len(r[0]) for r in rows) + 2
    widths = []
    for i, name in enumerate(cols):
        w = max(len(name), *(len(r[1][i]) for r in rows))
        widths.append(max(w, 10))

    total_w = label_w + sum(widths) + 2 * len(widths) + 2
    bar = "─" * min(total_w, 190)
    scheme = settings.get("weighting", "manual")
    scheme_txt = "" if scheme == "manual" else f" · {scheme}"

    print()
    print(f"  Backtest blend · {settings['mode']}{scheme_txt} · {settings['ppy']}/yr · rf {settings['rf'] * 100:.2f}%")
    print(f"  {bar}")
    print("  " + "Dataset".ljust(label_w) + "".join(f"{n:>{w + 2}}" for n, w in zip(cols, widths)))
    print(f"  {bar}")
    for label, vals in rows:
        line = "  " + label.ljust(label_w) + "".join(f"{v:>{w + 2}}" for v, w in zip(vals, widths))
        print(line)
    print(f"  {bar}")

    print("\n  Composition" + ("" if scheme == "manual" else f" · {scheme}"))
    wsum = sum(d.weight for d in datasets)
    for d in datasets:
        pct = (d.weight / wsum * 100) if wsum else 0
        note = ""
        if d.source == "trades":
            note = "  (curve built from trade P&L - risk metrics approximate)"
        elif d.source == "returns":
            note = "  (curve compounded from returns)"
        metric_note = ""
        if scheme == "riskparity":
            metric_note = f"   vol {_pct(d.stats.get('annVol'), 1)}/yr"
        elif scheme == "drawdown":
            metric_note = f"   max DD {_pct(d.stats.get('maxdd'), 1)}"
        elif scheme == "sharpe":
            metric_note = f"   Sharpe {_num(d.stats.get('sharpe'), 2)}"
        elif scheme == "calmar":
            metric_note = f"   Calmar {_num(d.stats.get('calmar'), 2)}"
        print(
            f"    {d.name:<16} {pct:6.1f}%{metric_note}   {d.path}"
            f"\n    {'':<16} {len(d.curve)} rows · {d.describe()}{note}"
        )
    cov = coverage.dropna().to_numpy() if len(coverage.dropna()) else np.array([])
    if cov.size:
        warm = int((cov < 0.999).sum())
        print(
            f"\n  Coverage: mean {cov.mean() * 100:.1f}% of weight live per period"
            f" (min {cov.min() * 100:.1f}%)"
            + (f", {warm} warmup periods (use --strict to trim)" if warm else "")
        )
    print(f"\n  {'Wrote: ' + ', '.join(out_paths) if out_paths else 'No chart written (--no-chart)'}")
    print()


# --------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------
def style():
    plt.rcParams.update({
        "figure.facecolor": BG,
        "axes.facecolor": PANEL,
        "savefig.facecolor": BG,
        "axes.edgecolor": LINE,
        "axes.labelcolor": MUTED,
        "axes.titlecolor": TEXT,
        "text.color": TEXT,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "grid.color": "#1c2231",
        "grid.linewidth": 0.8,
        "font.size": 9,
        "legend.frameon": False,
        "axes.titlesize": 10,
        "axes.titleweight": "bold",
    })


def make_chart(datasets, equity, bstats, settings, coverage, path, fmt, dpi, base):
    style()
    fig = plt.figure(figsize=(14, 12.5))
    gs = fig.add_gridspec(4, 2, height_ratios=[3.1, 1.7, 1.7, 2.4], hspace=0.42, wspace=0.16)
    ax_eq = fig.add_subplot(gs[0, :])
    ax_dd = fig.add_subplot(gs[1, :])
    ax_rs = fig.add_subplot(gs[2, :])
    ax_hm = fig.add_subplot(gs[3, :])

    # ---- equity: components rebased, blend bold on top
    active = [d for d in datasets if d.weight > 0]
    wsum = sum(d.weight for d in active) or 1.0
    if len(datasets) > 1:
        for d in active:
            c = d.curve
            scale = base / float(c.iloc[0])
            ax_eq.plot(c.index, c.to_numpy() * scale, color=d.color, lw=1.2, alpha=0.85,
                       label=f"{d.name} ({d.weight / wsum * 100:.0f}%)")
    ax_eq.plot(equity.index, equity.to_numpy(), color=WHITE if len(datasets) > 1 else datasets[0].color,
               lw=2.4, label="Blend" if len(datasets) > 1 else datasets[0].name, zorder=5)

    scheme = settings.get("weighting", "manual")
    scheme_txt = "" if scheme == "manual" else f" · {scheme}"
    ax_eq.set_title(
        f"Equity · rebased to {base:g}" if len(datasets) == 1
        else f"Equity · blended {settings['mode']}{scheme_txt}, rebased to {base:g}"
    )
    ax_eq.set_ylabel("Index")
    ax_eq.grid(True, axis="y", alpha=0.6)
    ax_eq.legend(loc="upper left", ncol=min(len(active) + 1, 4), fontsize=8.5)
    ax_eq.axhline(base, color=LINE, lw=0.8, ls=(0, (4, 4)))
    _date_axis(ax_eq, equity.index)

    # ---- drawdown
    uw = bstats["underwater"]
    idx = equity.index[: len(uw)]
    ax_dd.plot(idx, uw * 100, color=RED, lw=1.2)
    ax_dd.fill_between(idx, uw * 100, 0, color=RED, alpha=0.18)
    ax_dd.set_title(f"Drawdown · max {bstats['maxdd'] * 100:.2f}%")
    ax_dd.set_ylabel("%")
    ax_dd.grid(True, axis="y", alpha=0.6)
    _date_axis(ax_dd, idx)

    # ---- rolling sharpe
    if len(bstats["rollY"]):
        rx = bstats["rollX"]
        ax_rs.plot(rx, bstats["rollY"], color=PALETTE[0], lw=1.4)
        ax_rs.axhline(0, color=LINE, lw=0.9)
        ax_rs.axhline(1, color=GREEN, lw=0.8, ls=(0, (3, 3)), alpha=0.6)
    ax_rs.set_title(f"Rolling Sharpe · {bstats['rollW']}-period window")
    ax_rs.grid(True, axis="y", alpha=0.6)
    _date_axis(ax_rs, equity.index)

    # ---- monthly returns heatmap (with a YTD column, like index.html)
    table = monthly_table(equity)
    if table:
        years = sorted(table)
        mat = np.array([table[y] for y in years])
        # compound each year's months into the year total, ignoring gaps
        totals = np.array([
            float(np.prod(1.0 + row[np.isfinite(row)]) - 1.0) if np.isfinite(row).any() else np.nan
            for row in mat
        ])
        lim = np.nanmax(np.abs(mat)) if np.isfinite(np.nanmax(np.abs(mat))) else 0.05
        lim = max(lim, 1e-4)
        tlim = np.nanmax(np.abs(totals)) if np.isfinite(np.nanmax(np.abs(totals))) else 0.05
        tlim = max(tlim, 1e-4)
        cmap = LinearSegmentedColormap.from_list("pnl", [RED, "#12202a", GREEN])

        def shade(v, scale):
            return cmap(float(np.clip((v / scale + 1.0) / 2.0, 0.0, 1.0)))

        ax_hm.imshow(np.ma.masked_invalid(mat), cmap=cmap, vmin=-lim, vmax=lim, aspect="auto")
        for i, t in enumerate(totals):
            if np.isfinite(t):
                ax_hm.add_patch(Rectangle((11.5, i - 0.5), 1.0, 1.0,
                                          facecolor=shade(t, tlim), edgecolor="none"))
        ax_hm.axvline(11.5, color=LINE, lw=1.2)
        ax_hm.set_xlim(-0.5, 12.5)
        ax_hm.set_xticks(range(13))
        ax_hm.set_xticklabels(
            ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "YTD"],
            fontsize=8.5,
        )
        ax_hm.set_yticks(range(len(years)))
        ax_hm.set_yticklabels(years)
        ax_hm.set_title("Monthly returns · YTD = compounded year total")
        for i in range(len(years)):
            for j in range(12):
                v = mat[i, j]
                if np.isfinite(v):
                    ax_hm.text(j, i, f"{v * 100:.1f}", ha="center", va="center",
                               fontsize=7.5, color="#0b0e14" if abs(v) > lim * 0.45 else TEXT)
            t = totals[i]
            if np.isfinite(t):
                ax_hm.text(12, i, f"{t * 100:.1f}", ha="center", va="center",
                           fontsize=7.5, fontweight="bold",
                           color="#0b0e14" if abs(t) > tlim * 0.45 else TEXT)
        ax_hm.grid(False)
        for spine in ax_hm.spines.values():
            spine.set_visible(False)
    else:
        ax_hm.text(0.5, 0.5, "No parseable dates — monthly heatmap unavailable",
                   ha="center", va="center", color=MUTED, transform=ax_hm.transAxes)
        ax_hm.set_axis_off()

    # ---- footer line inside the figure
    first, last = equity.index[0], equity.index[-1]
    span = f"{pd.Timestamp(first):%Y-%m-%d} → {pd.Timestamp(last):%Y-%m-%d}" \
        if isinstance(equity.index, pd.DatetimeIndex) else f"{len(equity)} periods"
    fig.text(
        0.01, 0.005,
        f"bt.py · {span} · CAGR {_pct(bstats['cagr'], sign=True)} · Sharpe {_num(bstats['sharpe'])}"
        f" · Sortino {_num(bstats['sortino'])} · MaxDD {_pct(bstats['maxdd'])}"
        f" · {settings['ppy']} periods/yr · rf {settings['rf'] * 100:.2f}%",
        color=MUTED, fontsize=8.5,
    )

    written = []
    stem, _ = os.path.splitext(path)
    targets = {"all": ["png", "jpg", "svg", "pdf"], "both": ["png", "jpg"]}.get(fmt, [fmt])
    for f in targets:
        dest = f"{stem}.{f}"
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        save_kwargs = {"dpi": dpi, "bbox_inches": "tight"}
        if f in ("jpg", "jpeg"):
            save_kwargs["pil_kwargs"] = {"quality": 92}
        fig.savefig(dest, **save_kwargs)
        written.append(dest)
    plt.close(fig)
    return written


def _date_axis(ax, index):
    if isinstance(index, pd.DatetimeIndex) and len(index) > 1:
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
    for lbl in ax.get_xticklabels():
        lbl.set_fontsize(8.5)


def export_json(path, datasets, bstats, settings):
    def pack(st):
        keys = ("n", "years", "totalReturn", "cagr", "annVol", "sharpe", "sortino", "calmar",
                "maxdd", "best", "worst", "var95", "cvar95", "skew", "kurt", "winRate",
                "profitFactor", "expectancy", "payoff", "nTrades", "maxWinStreak", "maxLossStreak")
        out = {}
        for k in keys:
            v = st.get(k)
            if isinstance(v, (np.floating, np.integer)):
                v = float(v)
            if isinstance(v, float) and not math.isfinite(v):
                v = None
            out[k] = v
        return out

    payload = {
        "generated": pd.Timestamp.now(tz="UTC").isoformat(),
        "settings": {k: v for k, v in settings.items()},
        "datasets": [
            {
                "name": d.name,
                "path": d.path,
                "weight": float(d.weight),
                "weight_pct": (
                    float(d.weight / sum(x.weight for x in datasets) * 100.0)
                    if sum(x.weight for x in datasets) else 0.0
                ),
                "mapping": d.mapping,
                **pack(d.stats),
            }
            for d in datasets
        ],
        "blend": {
            **pack(bstats),
            "weighting": settings.get("weighting", "manual"),
            "weights": [
                {
                    "name": d.name,
                    "weight_pct": float(d.weight / (sum(x.weight for x in datasets if x.weight > 0) or 1.0) * 100.0),
                    "ann_vol": float(d.stats["annVol"]) if math.isfinite(float(d.stats.get("annVol", float("nan")))) else None,
                    "max_dd": float(d.stats["maxdd"]) if math.isfinite(float(d.stats.get("maxdd", float("nan")))) else None,
                    "sharpe": float(d.stats["sharpe"]) if math.isfinite(float(d.stats.get("sharpe", float("nan")))) else None,
                    "calmar": float(d.stats["calmar"]) if math.isfinite(float(d.stats.get("calmar", float("nan")))) else None,
                }
                for d in datasets if d.weight > 0
            ],
        },
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    return path


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(
        prog="bt.py",
        description="Blend CSV backtests into one weighted P&L curve and report stats + charts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  bt.py sample.csv
  bt.py a.csv b.csv -w 60,40
  bt.py a.csv b.csv -w a=70,b=30 --rf 2 --ppy 252
  bt.py a.csv b.csv --weighting riskparity   # or drawdown / sharpe / calmar
  bt.py *.csv --start 2022-01-01 --end 2023-12-31 -f both -o out/report.png
""",
    )
    p.add_argument("csv", nargs="+", help="CSV/TSV files to load (in weight order)")
    p.add_argument("-w", "--weight", action="append", default=[], metavar="SPEC",
                   help="allocations: '60,40' in file order, or 'name=60,name=40'. Repeatable. "
                        "Default: equal weight. Ignored with --weighting other than manual.")
    p.add_argument("--weighting", choices=["manual", "riskparity", "drawdown", "sharpe", "calmar", "equal"],
                   default="manual",
                   help="how blend weights are set: 'manual' (default, uses -w or equal), "
                        "'riskparity' (inverse volatility, w ∝ 1/σ), 'drawdown' (inverse max "
                        "drawdown, w ∝ 1/|maxDD|), 'sharpe' (w ∝ Sharpe), 'calmar' "
                        "(w ∝ CAGR/|maxDD|), or 'equal' (1/N); all measured over the "
                        "--start/--end range")
    p.add_argument("--rebalance", choices=["rebalanced", "buyhold"], default="rebalanced",
                   help="rebalanced (default) applies weights to each period's returns; "
                        "buyhold lets sleeves drift")
    p.add_argument("--ppy", type=int, default=252, help="periods per year (default 252)")
    p.add_argument("--rf", type=float, default=0.0, help="risk-free rate %% per year (default 0)")
    p.add_argument("--roll", type=int, default=63, help="rolling Sharpe window (default 63)")
    p.add_argument("--start", help="start date, inclusive (YYYY-MM-DD)")
    p.add_argument("--end", help="end date, inclusive (YYYY-MM-DD)")
    p.add_argument("--base", type=float, default=100.0, help="rebase level for charts (default 100)")
    p.add_argument("-o", "--out", default="report.png", help="chart path (default report.png)")
    p.add_argument("-f", "--format", choices=["all", "png", "jpg", "both", "svg", "pdf"], default="all",
                   help="chart format: 'all' (default) writes PNG, JPG, SVG and PDF; 'both' writes PNG+JPG; "
                        "or pick a single format")
    p.add_argument("--dpi", type=int, default=140, help="raster DPI (default 140)")
    p.add_argument("--json", metavar="PATH", help="also write metrics as JSON")
    p.add_argument("--strict", action="store_true",
                   help="drop the warmup so every strategy is live from the first plotted period")
    p.add_argument("--no-chart", action="store_true", help="skip chart rendering")
    p.add_argument("--list", action="store_true", help="print detected column mapping and exit")
    args = p.parse_args(argv)

    datasets = build_datasets(args.csv)
    if args.weighting == "manual":
        apply_weights(datasets, args.weight)
    else:
        if args.weight:
            print(
                f"bt.py: warning: -w/--weight ignored with --weighting {args.weighting}",
                file=sys.stderr,
            )
        for d in datasets:  # placeholder until the scheme is computed after date filtering
            d.weight = 1.0

    if args.list:
        for d in datasets:
            print(f"{d.name}: {len(d.curve)} rows -> {d.describe()}")
        return 0

    settings = {
        "mode": args.rebalance,
        "weighting": args.weighting,
        "ppy": args.ppy,
        "rf": args.rf / 100.0,
        "roll": args.roll,
    }

    # date filter, applied to every curve
    start = pd.Timestamp(args.start) if args.start else None
    end = pd.Timestamp(args.end) + pd.Timedelta(days=1) if args.end else None
    for d in datasets:
        c = d.curve
        if isinstance(c.index, pd.DatetimeIndex):
            if start is not None:
                c = c[c.index >= start]
            if end is not None:
                c = c[c.index < end]
            d.curve = c

    # risk-parity / drawdown / ratio weights use the date-filtered sample
    apply_weighting(datasets, args.weighting, args.ppy, settings["rf"])

    equity, frame, coverage = blend(datasets, args.rebalance, base=args.base, strict=args.strict)

    if len(datasets) > 1 and any(d.source == "trades" for d in datasets):
        print(
            "bt.py: warning: a dataset built from trade P&L is being blended with percentage-return"
            " curves; dollar P&L is not a return, so check the result",
            file=sys.stderr,
        )

    for d in datasets:
        d.stats = compute_stats(d.curve, args.ppy, settings["rf"], args.roll, d.trades)
    bstats = compute_stats(equity, args.ppy, settings["rf"], args.roll)

    if not bstats["valid"]:
        raise SystemExit("bt.py: not enough overlapping data to compute stats")

    written = []
    if not args.no_chart:
        written = make_chart(datasets, equity, bstats, settings, coverage, args.out, args.format, args.dpi, args.base)
    if args.json:
        written.append(export_json(args.json, datasets, bstats, settings))

    print_report(datasets, bstats, settings, coverage, written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
