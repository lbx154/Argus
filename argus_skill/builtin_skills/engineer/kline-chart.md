---
name: K-line Chart Skill (Engineer)
description: "[STUB -> moved to the quant vertical] Render candlestick (K-line) charts from OHLCV with MAs/volume and optional signal/buy-sell overlay; the real body is seeded only when the active vertical is quant."
category: quant-visualisation
version: 1
---

> **MOVED — this is a pointer stub, not the skill.**
>
> Per the skill-layering convention, `builtin_skills/` holds only cross-vertical
> (general) skills. This domain-specific skill now lives in the **quant vertical**:
>
> `argus_skill/verticals/quant/skills/engineer/kline-chart.md`
>
> Vertical-aware seeding copies the real body into the agent workspace at
> `argus_builtin_skills/engineer/kline-chart.md` **only when the active vertical is
> `quant`**, overwriting this stub. Edit the real body in the vertical, not here.
