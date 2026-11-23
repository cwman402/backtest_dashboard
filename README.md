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

## Command line (`bt.py`)

`bt.py` is the same analysis without a browser: it loads one or more CSVs,
blends them into one portfolio and prints stats plus a chart.

```bash
./bt.py a.csv b.csv -w 60,40                 # 60/40 split of every period's returns
./bt.py a.csv b.csv -w a=70,b=30 --rf 2      # weights by name, 2% risk-free
./bt.py a.csv b.csv --weighting riskparity   # inverse-volatility risk parity
./bt.py *.csv --ppy 12 --start 2022-01-01 -f both -o out/report.png
```

Weights follow the file order when given as a list (`-w 60,40`) or may name a
file/stem (`-w a=70,b=30`). With no `-w` the files are equal-weighted. The blend
applies the weights to each period's returns and renormalises across whichever
strategies are live, so a strategy that starts later simply joins at its first
date (`--strict` instead trims the warmup so everything starts together).

`--weighting` chooses how the sleeves are sized, always from the
`--start`/`--end` date-filtered sample and `--ppy`:

- `manual` (default) uses `-w`, or equal weights when `-w` is omitted.
- `riskparity` allocates inversely to annualised volatility, `wᵢ ∝ 1/σᵢ`
  (lower-vol strategies get more).
- `drawdown` allocates inversely to max drawdown, `wᵢ ∝ 1/|maxDDᵢ|`, so the
  strategy that lost the most gets the smallest share; a 0% drawdown is floored
  at 0.01% to keep the weight finite.
- `sharpe` allocates in proportion to the Sharpe ratio, `wᵢ ∝ Sharpeᵢ`
  (risk-free and `--ppy` aware), so a higher Sharpe gets more.
- `calmar` allocates in proportion to `CAGR / |maxDD|`, so a higher Calmar gets
  more.
- `equal` forces `1/N`.

Every scheme normalises to 100%. Strategies with a non-positive or undefined
volatility/ratio get a 0% share and sit out of the blend; if none has a usable
estimate the run falls back to equal weights. `-w` is ignored outside manual
mode, and `--json` records the scheme plus each sleeve's weight %, annualised
volatility, max drawdown, Sharpe and Calmar — mirroring the dashboard's export.

| Flag | Meaning |
|------|---------|
| `-w, --weight` | allocations, list or `name=pct` pairs (repeatable); manual mode only |
| `--weighting` | `manual` (default), `riskparity`, `drawdown`, `sharpe`, `calmar` or `equal` |
| `--rebalance` | `rebalanced` (default) or `buyhold` (weights drift) |
| `--ppy` | periods per year: 252 default, use 12 for monthly, 52 weekly, 365 crypto |
| `--rf` | risk-free rate % per year (default 0) |
| `--roll` | rolling Sharpe window in periods (default 63) |
| `--start`, `--end` | inclusive date filter |
| `-o`, `-f`, `--dpi` | output path, format (`all` default = png+jpg+svg+pdf, or `png`/`jpg`/`both`/`svg`/`pdf`), raster DPI |
| `--json PATH` | also write metrics JSON |
| `--strict` | drop the warmup so every strategy is live from the first period |
| `--list` | show the auto-detected column mapping and exit |
| `--no-chart` | stats only |

The chart is a single figure with the equity curves (components + blend),
drawdown, rolling Sharpe and a monthly-return heatmap. Every run writes it as
`report.png`, `report.jpg`, `report.svg` and `report.pdf` unless `-f` picks a
single format. Column detection,
percent/fraction handling and the metric formulas are the same as `index.html`;
`bt.py` needs only Python 3 with pandas, numpy and matplotlib.

Caveats: when a curve has to be built from trade P&L the risk metrics are
approximate (they assume one unit per period), and the blend aligns strategies
on the union of their dates — mixing daily and monthly files is allowed but the
result is only meaningful if the frequencies match.

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

- **Equity curve** — all loaded datasets overlaid; click a legend item (or the
  chip's eye icon) to show/hide a dataset.
- **Drawdown** — underwater curve of the active dataset.
- **Rolling Sharpe** — window configurable in the controls.
- **Return distribution** — histogram of period returns.
- **Monthly returns** — year × month heatmap with YTD column.
- **Trade P&L distribution** and a **trades/periods table**.

## Compare mode

- **Rebased** (default) normalises every curve to a common base (configurable,
  default 100) so strategies with different price levels line up; **Raw** shows
  absolute values.
- Toggle each dataset's visibility with the eye icon or the legend.

## Weighted blend

When multiple datasets are loaded, set an allocation weight on each dataset chip
(percentages, shown with a Σ total) and switch **Blend** to **On**:

- The blend is the weighted sum of each dataset's **period returns**,
  renormalised across whichever datasets have data at that date, so a strategy
  that starts later simply joins at its first date.
- It appears as a white line in the equity chart; pick `— Blend · weighted —` in
  the **Dataset** dropdown to drive the metrics, drawdown, rolling Sharpe and
  trade panels from it.
- The CLI (`bt.py --rebalance buyhold`) can also let weights drift instead of
  rebalancing each period, and offers the same schemes with
  `--weighting manual|riskparity|drawdown|sharpe|calmar|equal`.

### Weighting schemes

The **Weighting** dropdown next to **Blend** chooses how the blend is sized:

- **Manual weights** — use the percentage typed on each dataset chip (default).
- **Risk parity · inverse vol** — allocate inversely to each dataset's
  annualised volatility over the selected date range, i.e. `wᵢ ∝ 1 / σᵢ`,
  normalised to 100%. Lower-volatility strategies get a larger share, so each
  contributes a similar amount of risk.
- **Inverse max drawdown** — `wᵢ ∝ 1 / |maxDDᵢ|`, so strategies with deeper
  losses get less; a 0% drawdown is floored at 0.01% to avoid an infinite
  weight.
- **Sharpe weighted** — `wᵢ ∝ Sharpeᵢ` (using the risk-free rate and PPY
  settings), so a higher Sharpe gets more. Non-positive Sharpe gets 0%.
- **Calmar weighted** — `wᵢ ∝ CAGR / |maxDDᵢ|`, so a higher Calmar gets more.
  Non-positive Calmar gets 0%.
- **Equal weight** — `1/N` across all valid datasets.

Computed weights appear read-only on the dataset chips (with the driving
volatility / drawdown / ratio in the tooltip and the composition note); changing
the date range or the risk-free/PPY settings recomputes them. Choosing a scheme
switches the blend on automatically; turning the blend off returns to manual
weights. The scheme and the resulting weights (with annualised volatility, max
drawdown, Sharpe and Calmar) are included in the exported metrics JSON.

## Export

- **Export metrics** downloads a `backtest_metrics.json` containing the computed
  statistics and detected mapping for every dataset, the portfolio composition
  and performance, and the active settings.
- **Print** produces a print-friendly report.

## Notes

- CSV delimiter is auto-detected (`,` `;` tab `|`); quoted fields and `%`/`$`/
  thousands separators are handled.
- For additive P&L files with no NAV column, risk metrics are approximate
  (they assume a 100-unit starting notional) but win rate, profit factor,
  expectancy and other trade statistics are computed directly from the P&L.
- Everything runs locally; there is no network access.
