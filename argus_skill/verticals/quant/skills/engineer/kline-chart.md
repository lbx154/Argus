---
name: K-line Chart Skill (Engineer)
description: Render candlestick (K-line) charts from OHLCV — moving averages, volume, and (optionally) the model signal / buy-sell markers overlaid — for quant reports and for eyeballing what a strategy actually trades. Backed by verticals/quant/charting.py (mplfinance, headless Agg).
category: quant-visualisation
version: 1
scientist_model: gpt-5.5
created_at: 2026-07-13T00:00:00+00:00
---

## Title
K-line Chart Skill (Engineer)

## Description
Use this when a quant mission needs to **show**, not just describe, price action or
what a strategy is doing — a report figure, a sanity look at a name, or a picture of
where a signal says to buy/sell. Renders a report-quality candlestick chart to a PNG.

## API (`argus_skill/verticals/quant/charting.py`)
- `candlestick_chart(ohlcv, out_path, *, title, mavs=(5,20,60), volume=True, signal=None, buy=None, sell=None)`
  — `ohlcv` is a DataFrame with open/high/low/close[/volume] (any case), date index.
  `signal` (a date→value Series) draws in a lower panel; `buy`/`sell` (date lists)
  mark ▲/▼ on the price.
- `chart_from_dump(code, start, end, out_path, *, provider_uri=None, **kwargs)`
  — pull one qlib instrument's OHLCV straight from the local dump and chart it;
  extra kwargs pass through (`mavs`, `signal`, `buy`, `sell`, `title`).

## Guidance
- **Always overlay the signal / trades when the point is the strategy.** A bare
  candlestick describes the market; `signal=`/`buy=`/`sell=` shows what the *model*
  is doing against it — that is the figure a reviewer/user actually learns from.
- Title every chart with the instrument + window (e.g. `"SH600519 2025-06..2026-06"`).
- Save under the mission's artifacts directory and reference the path in the report.
- Headless-safe (Agg backend); never blocks on a display.

## Example
```python
from argus_skill.verticals.quant.charting import chart_from_dump
chart_from_dump("SH600519", "2025-06-01", "2026-06-04", "artifacts/moutai.png",
                mavs=(5, 20, 60), buy=buy_dates, sell=sell_dates, signal=score_series)
```
