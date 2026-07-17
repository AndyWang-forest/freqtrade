# Strategy Research Agent Runtime

This directory contains the versioned source for the local Freqtrade strategy
research agent. Runtime copies live under `user_data/strategy_research/` after:

```bash
tools/strategy_research_agent/install_runtime.sh
```

`user_data/` remains the local runtime area for market data, reports,
dashboards, backtest exports, queues, and private config. Agent capabilities
that must survive a new machine belong here under `tools/strategy_research_agent/`.

## Fixed Research Contract

- Market: Binance USDT-M futures only.
- Margin: isolated.
- Leverage: fixed 50x; generated strategy classes must cap `leverage()` at 50x.
- ROI: `{"0": 1.20, "180": 1.50, "360": 1.00}`.
- Stoploss: `-0.60`.
- Entry timeframes: `3m`, `5m`, `15m`; current generated experiments default to `15m`.
- Background timeframes: `1h` may be used only as confirmation/context, not as primary entry.
- Promotion is family-level: target-regime edge plus hostile-regime loss containment, not naked all-regime performance.

## Pair Universe

- Core futures pairs: `BTC/USDT:USDT`, `ETH/USDT:USDT`.
- Research extension futures pairs: `SOL/USDT:USDT`, `BNB/USDT:USDT`, `XRP/USDT:USDT`.
- Default research scope: `core`.
- Extension research must be explicit with `--pair-scope extension` or `--pair-scope research_all`.
- Extension pairs are for research generalization and event validation only. They do not modify dry-run/live configs, registry candidates, or promotion status.
- Excluded classes remain out even when volume is high: meme coins, low-liquidity altcoins, new listings, synthetic stock/commodity contracts, and unstable/non-crypto derivative contracts.

Example:

```bash
user_data/strategy_research/start_manual_research.sh --factor-research --extra-agent-arg --pair-scope --extra-agent-arg research_all
user_data/strategy_research/start_manual_research.sh --event-study --extra-agent-arg --pair-scope --extra-agent-arg research_all
```

Factor and event research may additionally select a data-derived market state:

```bash
user_data/strategy_research/start_manual_research.sh --factor-research \
  --extra-agent-arg --pair-scope --extra-agent-arg research_all \
  --extra-agent-arg --regime-label --extra-agent-arg bull
user_data/strategy_research/start_manual_research.sh --event-study \
  --extra-agent-arg --pair-scope --extra-agent-arg research_all \
  --extra-agent-arg --regime-label --extra-agent-arg bull
```

Indicators are computed on the full causal history; only entries whose entire
forward-study horizon remains inside an active manifest window are evaluated.

`--agent-brain` also preserves the selected pair scope when it refreshes factor
research internally, so a long run cannot silently overwrite `latest_factor_*`
back to `core` after an explicit `research_all` cycle.

## Required Preload

Every strategy research entrypoint first runs:

```bash
user_data/strategy_research/preflight_research_agent.py
user_data/strategy_research/enforce_agent_workflow_gate.py
```

The gate requires these fixed artifacts to be loadable:

- knowledge graph
- research memory
- consolidation policy
- workflow contract
- data-derived regime window manifest
- regime inference quarantine manifest
- E1-E41 research program postmortem
- independent research-family allocation
- current research failure funnel and blocker fingerprint
- weekly knowledge update layer

The knowledge graph is a multi-domain external brain, not only a price-action
notebook. Normal strategy research requires active cards in these domains:

- price action
- regime routing
- crypto derivatives structure: funding, OI, liquidation, mark/index basis
- market microstructure and execution cost: spread, slippage, order book depth, minimum edge
- cross-asset lead-lag and common market-factor context
- Freqtrade runtime execution hooks and config override checks

When a hypothesis uses non-OHLCV concepts, the Agent must verify the required
data coverage first. Missing funding/OI/L2/basis/router/runtime data downgrades
the idea to research-only diagnostics or event study; it cannot become strategy
code from theory alone.

## Current Workflow

1. Preflight.
2. Run the current market-state family router to determine deployment permission, including no-trade.
3. Rebuild the E1-E41 postmortem and run the independent allocator to choose one under-covered family and its data-derived historical home regime.
4. Load knowledge graph, research memory, and consolidation rules.
5. Run allocator-targeted factor research on `3m`/`5m`/`15m` futures using the typed price-action, regime, derivatives, microstructure, and cross-asset registry.
6. Freeze factor thresholds on the earliest chronological home episode; test gross edge on de-clustered events, then realistic costs and unchanged-threshold replication on later independent regime windows.
7. Treat any gross-positive single-factor row as supporting evidence only. It may enter structural composition even when its isolated cost or replication gate fails, but it has no standalone authority; compose its frozen condition with a predeclared structural event for the allocated strategy family.
8. Repeat gross, realistic-cost, and independent validation-window gates on that family-factor composite, and verify any auxiliary input has a causal Freqtrade runtime path.
9. Refresh the failure funnel. If there is no current validated composite event or the blocker fingerprint is unchanged, stop adjacent variant generation. All-history scans remain diagnostics and cannot replace current-allocation reports.
10. Refresh data-derived regime windows and quarantine legacy regime interpretations.
11. Generate memory-guided strategy variants only when their `source_event_id` matches a current validated family-factor composite event; refreshed knowledge/memory alone cannot authorize code generation.
12. Backtest through Freqtrade.
13. Run event-to-Freqtrade execution alignment when an event definition exists.
14. Run post-run attribution.
15. Run failure attribution.
16. Run recursive-analysis and lookahead-analysis for candidates.
17. Run walk-forward validation.
18. Run fee/slippage/funding stress through the promotion/family gate.
19. Run family risk gate. Native finite protection evidence must come from Freqtrade; permanent-disable replay is a separate diagnostic.
20. Run promotion gate.
21. Update strategy lineage, research memory, consolidation, dashboard, and registry.

Family-risk and promotion gate results are research evidence even when they
fail. Strategies in the current registered family-gate CSV enter lineage as
`research_evidence` even when they are not in registry. A failed gate must still
rebuild lineage, research memory, and consolidation before the dashboard/report
refresh so blockers become durable experience for the next loop.

## Supported Entrypoints

```bash
user_data/strategy_research/start_manual_research.sh --preflight-only
user_data/strategy_research/start_manual_research.sh --quick
user_data/strategy_research/start_manual_research.sh --source-scout
user_data/strategy_research/start_manual_research.sh --price-action-knowledge
user_data/strategy_research/start_manual_research.sh --bilibili-transcripts
user_data/strategy_research/start_manual_research.sh --knowledge-graph
user_data/strategy_research/start_manual_research.sh --knowledge-guided-hypotheses
user_data/strategy_research/start_manual_research.sh --factor-research
user_data/strategy_research/start_manual_research.sh --factor-to-strategy
user_data/strategy_research/start_manual_research.sh --failure-funnel
user_data/strategy_research/start_manual_research.sh --event-study
user_data/strategy_research/start_manual_research.sh --chan-event-study
user_data/strategy_research/start_manual_research.sh --event-execution-alignment
user_data/strategy_research/start_manual_research.sh --regime-windows
user_data/strategy_research/start_manual_research.sh --current-market-router
user_data/strategy_research/start_manual_research.sh --agent-brain
user_data/strategy_research/start_manual_research.sh --weekly-knowledge-update
user_data/strategy_research/start_manual_research.sh --walk-forward
user_data/strategy_research/start_manual_research.sh --promotion-gate \
  --extra-agent-arg --csv --extra-agent-arg user_data/strategy_research/reports/<experiment>.csv
user_data/strategy_research/start_manual_research.sh --family-risk-gate \
  --extra-agent-arg --csv --extra-agent-arg user_data/strategy_research/reports/<experiment>.csv
user_data/strategy_research/start_manual_research.sh --a1-external-permission
user_data/strategy_research/start_manual_research.sh --trade-behavior
user_data/strategy_research/start_manual_research.sh --failure-attribution
user_data/strategy_research/start_manual_research.sh --post-run-attribution
user_data/strategy_research/start_manual_research.sh --mature-researcher
user_data/strategy_research/start_manual_research.sh --mature-researcher-queue
user_data/strategy_research/start_manual_research.sh --execute-mature-researcher
user_data/strategy_research/start_manual_research.sh --strategy-lineage
user_data/strategy_research/start_manual_research.sh --research-memory
user_data/strategy_research/start_manual_research.sh --memory-guided-hypotheses
user_data/strategy_research/start_manual_research.sh --memory-guided-strategies
```

Removed legacy entrypoints must not be reintroduced without a new PR and a clear
workflow reason: broad smoke wrappers, all-in-one cycle wrappers, agenda
executors, manual playbook generators, behavior-plan generators, and separate
K-line lab wrappers.

## Long-Running Research Daemon

For unattended research-only loops, use:

```bash
PAIR_SCOPE=research_all DURATION_HOURS=12 CYCLE_MINUTES=45 \
  user_data/strategy_research/run_tonight_research_daemon.sh
```

The daemon rotates through agent brain, factor research, event study,
factor-to-strategy planning, mature researcher queue/execution, post-run
attribution, and family risk gate. It runs preflight and current-market router
before each cycle, then refreshes memory, lineage, and dashboard.

Each successful cycle snapshots the important `latest_*` artifacts into the run
directory:

```text
user_data/strategy_research/daemon_runs/<UTC_START>/artifacts/cycle_###_<mode>/
```

This preserves per-cycle evidence even though runtime reports such as
`latest_factor_research.md` and `latest_event_study.md` intentionally remain
"latest pointer" files. For pair-scope sensitive modes, the daemon validates
that the report JSON still matches the requested `PAIR_SCOPE`; a mismatch fails
the cycle instead of being hidden by a later report overwrite.

## A1 External Permission Research

The A1 failed-bounce short family can be tested behind an external
regime-permission artifact instead of hardcoding long-history regime logic
inside a Freqtrade strategy class:

```bash
user_data/strategy_research/start_manual_research.sh --a1-external-permission
```

This mode refreshes the data-derived regime manifest check, builds the A1
permission artifact from BTC/ETH futures context, runs the current A1
permission strategy validation, evaluates it with the family risk gate, and
then refreshes lineage, research memory, consolidation, and the dashboard. It
does not change dry-run/live config and does not promote a strategy without the
separate dry-run risk preflight and manual approval step.

`family_risk_gate.py` accepts both older main/walk-forward/regime CSVs and
newer manifest/recent CSV rows. Manifest family-home rows serve as target-regime
evidence and other active labels serve as hostile evidence. The first explicit
`--csv` run writes a SHA-256-locked source pointer; later reruns reuse that exact
content and fail if it changes. Rows that only have
aggregate simulation are allowed for research reporting, but rows with trades
still require trade-level artifacts before dry-run review.

## Current Evidence Outputs

- Dashboard: `user_data/strategy_research/dashboard/index.html`
- Reports: `user_data/strategy_research/reports/`
- Factor research: `user_data/strategy_research/factors/latest_factor_research.md`
- Factor-to-strategy plan: `user_data/strategy_research/factors/latest_factor_strategy_plan.md`
- Research failure funnel: `user_data/strategy_research/failure_funnel/latest_research_failure_funnel.md`
- E1-E41 postmortem: `user_data/strategy_research/postmortems/latest_research_program_postmortem.md`
- Research allocator: `user_data/strategy_research/research_allocation/latest_research_family_allocator.md`
- Event study: `user_data/strategy_research/event_studies/latest_event_study.md`
- Chan third-point event study: `user_data/strategy_research/event_studies/latest_chan_third_point_event_study.md`
- Current market router: `user_data/strategy_research/reports/latest_current_market_state_family_router.md`
- Regime windows: `user_data/strategy_research/regime_windows/latest_regime_windows.md`
- Regime quarantine: `user_data/strategy_research/regime_windows/regime_inference_quarantine.md`
- Walk-forward: `user_data/strategy_research/walk_forward_summaries/latest_walk_forward_summary.md`
- Family risk gate: `user_data/strategy_research/family_risk_gate/latest_family_risk_gate.md`
- Promotion report: `user_data/strategy_research/promotion_reports/latest_promotion_report.md`
- Trade behavior: `user_data/strategy_research/trade_behavior/latest_trade_behavior.md`
- Failure attribution: `user_data/strategy_research/failure_attribution/latest_failure_attribution.md`
- Mature researcher: `user_data/strategy_research/mature_researcher/latest_researcher_decision.md`
- Mature queue: `user_data/strategy_research/mature_researcher/latest_response_queue.md`
- Strategy lineage: `user_data/strategy_research/strategy_library/latest_strategy_lineage.md`
- Research memory: `user_data/strategy_research/research_memory/latest_research_memory.md`
- Weekly knowledge update: `user_data/strategy_research/knowledge_updates/latest_weekly_knowledge_update.md`
- Consolidation: `user_data/strategy_research/consolidation/latest_research_consolidation.md`

## Regime Window Builder

Regime labels are generated from local Binance USDT-M BTC/ETH futures OHLCV,
not from hardcoded historical examples. Refresh them with:

```bash
user_data/strategy_research/start_manual_research.sh --regime-windows
```

The builder strictly prefers `1h` futures feather data and resamples `15m`/`5m`/`1m`
futures candles to `1h` when needed. It computes BTC/ETH returns, EMA gaps,
realized volatility, ATR%, BB width, trend strength, range score, and
directional agreement before selecting candidate `bull`, `bear`, `range`, and
`high_vol` windows. Percentile features are causal rolling percentiles, trend
direction and high volatility are separate labels, and validation episodes for
the same family cannot overlap in calendar time.

Gate provenance is SHA-256 locked. Runner-provided execution-alignment targets
are stored in the registered experiment metadata, preserved by an explicit
family-gate rerun, and consumed by post-run attribution. Trade behavior defaults
to the primary-cost artifacts referenced by that same registered experiment.

Old manually named windows such as `bull_home`, `range_home`, `bear_home`, and
`high_vol_hostile` are quarantined. Their old reports may remain as raw
date-range backtests, but they must not be used as active regime truth,
promotion evidence, strategy-generation basis, or durable memory until relabeled
against `latest_regime_windows.json`.

## Current Market-State Family Router

Before strategy-family experiments, refresh the router:

```bash
user_data/strategy_research/start_manual_research.sh --current-market-router
```

The router reads local Binance USDT-M BTC/ETH futures `5m` and `15m` data,
computes recent returns, EMA structure, realized volatility, ATR%, BB width,
trend efficiency, intraday green share, recent range position, and volume
context, then classifies the current market as `bear_continuation`,
`bear_relief_or_mixed`, `bull_trend`, `range_or_compression`,
`high_vol_mixed`, or `mixed_unknown`.

It maps that state to strategy-family decisions such as A1/C1/D1 short watch,
C2 future bull module, B range-only research, E compression research, or F
defense/no-trade. `off_or_wait` and no-trade are valid outputs; they prevent
the Agent from forcing A1/C1/D1 experiments into incompatible regimes. The report
is research-only and never changes registry, dry-run, live config, or strategy
code.

## Dry-Run Runtime Safety

The futures dry-run helper is installed to:

```bash
user_data/start_futures_dryrun.sh
```

It sources `~/.freqtrade_telegram_env`, runs a Binance futures ccxt preflight
through the active proxy/VPN environment, and refuses to start if the futures
data path is unavailable. A running process or UI pong is not enough.

Before the helper starts the bot, it also runs:

```bash
user_data/strategy_research/dryrun_strategy_risk_preflight.py
```

The same check can be run manually for all registry candidates:

```bash
user_data/strategy_research/start_manual_research.sh --dryrun-risk-preflight
```

This preflight loads the strategy through Freqtrade, compares strategy-defined
values with config overrides, and blocks dry-run startup if the final effective
contract is not futures/isolated/50x with fixed ROI, fixed stoploss,
exchange-side stoploss, callable custom exits, and the three-stoploss guard.

Important runtime safety settings:

- `ccxt_config.requests_trust_env=true`
- `ccxt_async_config.aiohttp_trust_env=true`
- `order_types.stoploss=market`
- `order_types.stoploss_on_exchange=true`
- `order_types.stoploss_price_type=mark`

Before live review, config parsing is not enough. A tiny-size exchange-side
operation must verify that a filled futures position receives an exchange-side
stop order.

## Live-Review Candidate PR Boundary

If a dry-run passes, do not commit the running bot state. Open a secret-free
live-review candidate PR instead.

Versioned artifacts may include:

- strategy source code
- registry/candidate status such as `dryrun_candidate` or `live_review_candidate`
- promotion and family-risk gate summary evidence
- dry-run/live config templates with no secrets
- live-review checklist
- rollback and emergency-stop runbook

Local-only artifacts must not be committed:

- API keys, exchange secrets, Telegram token, or chat_id
- running process state
- trade/runtime sqlite databases
- full local dashboards, bulky backtest exports, and unsanitized private reports

Live activation always remains a separate manual approval step.
