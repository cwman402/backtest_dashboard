# Backtest Dashboard

A single-file, fully client-side backtest reviewer. Drop one or more CSV
exports from your backtests into `index.html` and immediately get an equity
curve, drawdown, risk metrics, monthly heatmap and trade statistics — all
computed in the browser. **No install, no server, no dependencies; nothing is
uploaded or stored.**

## Usage

Open `index.html` directly in any modern browser (double-click, or
`file:///.../backtest_dashboard/index.html`). Then either:

- **Open CSV** (or drag & drop files anywhere on the window), or
- click **Load demo** to explore with synthetic data.

Keyboard shortcuts: `O` = open files, `D` = demo.

Multiple files can be loaded at once; each becomes a dataset you can compare.

## What it accepts

Column detection is automatic, and every dataset exposes **overridable
dropdowns** for the mapping. Recognised columns:

| Role | Auto-detected from names like | Notes |
|------|-------------------------------|-------|
| **date** | `date`, `time`, `timestamp`, `period`, `week`, `month` | Also detected from parseable date *values* (ISO, `YYYY-MM-DD`, `MM/DD/YYYY`, `Mon DD, YYYY`, timezone-suffixed). |
| **equity** | `equity`, `nav`, `value`, `portfolio`, `balance`, `capital`, `wealth` | A running level (NAV). If absent, derived from returns or trade P&L. |
| **returns** | `return`, `ret`, `pnl_pct`, `pct`, `change` | Compounded into an equity curve. Prefers a column named `...strategy...`. Auto-detects fraction vs. percent. |
| **trade pnl** | `pnl`, `trade_pnl`, `profit`, `realized`, `pl` | Per-trade or per-period P&L for trade stats. If there is no equity column, a curve is built as `base + cumulative P&L`. |
| **benchmark** | `benchmark`, `buyhold`, `spx`, `spy`, `index` | Rebased to the strategy's start and overlaid (dashed line) for buy & hold / alpha. |

If a column mapped as "equity" is actually a P&L/return stream (has many
non-positive values), it is automatically demoted to trade P&L and a proper
cumulative curve is built.

## Metrics

Total return, CAGR, annualised volatility, Sharpe, Sortino, Calmar, max
drawdown, best/worst period, win rate, profit factor, payoff, expectancy, VaR
95%, CVaR 95%, max win/loss streaks, skew, kurtosis, and (when a benchmark is
mapped) buy & hold and alpha.

Configurable inputs: **periods per year** (252 daily default, or 365/52/12/4/8760/custom),
**risk-free rate**, **rolling window**, and a **date range** filter.

## Charts

- **Equity curve** — all loaded datasets overlaid; click a legend item to toggle.
- **Drawdown** — underwater curve of the active dataset.
- **Rolling Sharpe** — window configurable in the controls.
- **Return distribution** — histogram of period returns.
- **Monthly returns** — year × month heatmap with YTD column.
- **Trade P&L distribution** and a **trades/periods table**.

## Compare mode

- **Rebased** (default) normalises every curve to a common base (default 100) so
  strategies with different price levels line up; **Raw** shows absolute values.
- Toggle each dataset's visibility with the eye icon or the legend.

## Export

- **Export metrics** downloads a `backtest_metrics.json` with every computed
  statistic per dataset (plus the detected mapping and settings).
- **Print** produces a print-friendly report.

## Notes

- CSV delimiter is auto-detected (`,` `;` tab `|`); quoted fields and `%`/`$`/
  thousands separators are handled.
- For additive P&L files with no NAV column, risk metrics are approximate
  (they assume a 100-unit starting notional) but win rate, profit factor,
  expectancy and other trade statistics are computed directly from the P&L.
- Everything runs locally; there is no network access.
