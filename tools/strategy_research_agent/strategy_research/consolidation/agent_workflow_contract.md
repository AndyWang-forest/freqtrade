# Strategy Agent Workflow Contract

This is the durable interpretation of the current strategy research Agent.

## Current Architecture

The Agent is not being built from zero. It already has:

- A materials layer with local transcripts, web snapshots, and user-provided local research documents.
- A knowledge layer with cleaned claims, knowledge cards, and a price-action knowledge graph.
- A self-iteration loop that can generate hypotheses, run Freqtrade research backtests, diagnose failures, plan improvements, and maintain candidate/watchlist/rejected queues.

The current improvement is to integrate those existing parts into one stronger research workflow:

1. Read the knowledge graph to source professional trading ideas.
2. Read research memory to understand repeated failures, avoid rules, and next blockers.
3. Read the consolidation layer to enforce hard research boundaries and required validation gates.
4. Use the current market-state router only to determine present deployment permission, including a valid no-trade result.
5. Rebuild the E1-E41 program postmortem, then use the independent research-family allocator to choose one under-covered family, historical home regime, and permitted side for research.
6. Evaluate typed knowledge-derived features in price-action, regime, derivatives, microstructure, and cross-asset domains.
7. Freeze factor thresholds on the earliest chronological home episode, then check de-clustered gross edge, realistic costs, and unchanged-threshold replication on later independent regime windows.
8. Treat every gross-positive single-factor distribution as supporting evidence only. It may enter structural composition even if its isolated cost or replication gate fails, but it has no standalone authority; compose its frozen condition with a predeclared structural event for the allocated strategy family.
9. Repeat gross, realistic-cost, and independent-window validation on the family-factor composite, then prove that every auxiliary input has a causal Freqtrade runtime path.
10. Generate strategy code only when the hypothesis carries a `source_event_id` that matches the current validated family-factor composite plan. Knowledge, memory, or a single factor alone are not executable authority.
11. Only composite events with forward-distribution and runtime-compatibility evidence may pass into the existing self-iteration loop: isolated strategy generation, Freqtrade backtesting, event-to-execution alignment, post-run attribution, improvement planning, and promotion-gate review.

Before selecting the next experiment, the Agent must refresh the research
failure funnel. The funnel classifies the current blocker as `gross_fail`,
`cost_killed`, `validation_reversal`, `data_blocked`,
`execution_incompatible`, or `gate_semantic_block`. A neighboring strategy
filter may be generated only when a current validated factor event exists and
the blocker fingerprint has changed. Otherwise the Agent must gather new
causal evidence, continue preregistered data collection, or accept no-trade.

All-history factor/event scans are diagnostics only. They are versioned by
context and cannot overwrite the current allocator-targeted report pointer.

## Reflection And Allocation Gate

The deployment router and research allocator answer different questions. The
router asks whether a registered family is permitted to trade in the current
market. The allocator asks which missing family deserves the next evidence
budget across historical, data-derived home regimes. A current no-trade result
must never prevent research into an under-covered family, and a research
allocation must never enable trading.

Before allocating another experiment, the Agent must classify E1-E41 by family,
evidence stage, mechanism, data source, and outcome. Three consecutive
edge-readable failures using the same family, mechanism, and data source suspend
adjacent variants in that lane. `data_blocked` and sample/causality blocks do
not count as failed edge. E1 and E33 remain frozen research assets; E23 and E32
wait for genuinely new prospective evidence and must not be rerun unchanged.

## Event Study Gate

Concrete strategy generation is not the first research step. The Agent must first prove that an entry event has statistical edge using event-study evidence:

- event sample count;
- forward returns at short horizons;
- win rate;
- MFE/MAE distribution;
- pair, side, timeframe, and regime notes;
- fee/slippage sensitivity before any candidate promotion.
- gross expectancy before any cost deduction;
- independent event count after overlapping forward horizons are de-clustered;
- replication in at least two independent windows for the selected regime, including one validation episode that did not participate in threshold calibration;
- realistic cost is the primary edge screen; stress cost is a safety check, not
  the primary edge score. Promotion still requires stress evidence, with home
  episode total no worse than `-10%` and worst episode no worse than `-15%`.

If an event does not clear the edge gate, the Agent may only use it as a counterexample, redesign input, or negative-control experiment. It must not turn that event into another strategy class just because the knowledge card sounds plausible.

## Event-To-Execution Alignment Gate

Local pandas/event-study evidence is not enough to promote a strategy. After a
Freqtrade backtest, the Agent must reconcile the event timestamps with actual
Freqtrade trades whenever the strategy came from a measurable event definition.

This gate must explain:

- how many event-study signals became actual Freqtrade trades;
- how many were blocked by startup candles, an already-open trade,
  max-open-trades behavior, protections, or order/execution timing;
- whether the executed events kept the event-study forward-return edge after
  Freqtrade entries, exits, ROI, stoploss, time-stop, fees, and funding;
- whether local event-study results should be treated as executable evidence,
  clustered evidence, or redesign-only evidence.

If event-study edge disappears after Freqtrade execution alignment, the result
must not be promoted by only citing the local event-study table.

## Timeframe Contract

For 50x Binance USDT-M futures research, concrete strategy entry must use short-cycle K-line granularity:

- Allowed primary entry timeframes: `3m`, `5m`, `15m`.
- `1h` may only be used as a background, regime, or confirmation timeframe.
- `1h`, `4h`, and `1d` must not be used as the primary entry timeframe for new 50x strategy classes.

If an event study is based on `1h` candles, the Agent may only use it as regime context or as a negative/control study until the entry trigger is translated to `3m`, `5m`, or `15m`.

## Pair Universe Contract

The default pair universe is the core Binance USDT-M futures set:

- `BTC/USDT:USDT`
- `ETH/USDT:USDT`

High-liquidity extension pairs are available only when a run explicitly asks
for `--pair-scope extension` or `--pair-scope research_all`:

- `SOL/USDT:USDT`
- `BNB/USDT:USDT`
- `XRP/USDT:USDT`

Extension pairs are research-generalization and event-validation evidence. They
do not change dry-run/live config, registry candidates, or promotion status by
implication. Any extension-pair strategy or registry entry must pass separate
family-risk gate, promotion gate, dry-run risk preflight, and manual review.

High liquidity is necessary but not sufficient for inclusion. The Agent must
keep meme coins, low-liquidity altcoins, new listings, synthetic stock or
commodity contracts, and unstable/non-crypto derivative contracts outside the
fixed research universe unless a new PR changes this policy explicitly.

## Family Exit-Risk Contract

Peak-profit drawdown exits are strategy-family controls, not a global futures
risk parameter. The fixed ROI, stoploss, leverage, and other global risk
settings remain unchanged.

- Peak protection defaults to `off` for every strategy family.
- A1 `downtrend_failed_bounce_short` may use the validated `peak40` preset:
  activate at `+0.40` margin return and exit after a `0.40` giveback from peak.
- E `volatility_compression_directional_expansion` must keep Peak protection off. Its edge
  depends on retaining directional expansion tails, and the unchanged-entry
  Peak40 comparison cut those winners early.
- Other strategy families may test a small named Peak preset only through an
  unchanged-entry A/B comparison. Research evidence alone does not authorize
  dry-run: the tracked family contract must be updated before promotion.
- Custom or unnamed Peak thresholds cannot pass dry-run risk preflight.

The Agent must compare entry-identical original and Peak variants across home
episodes, realistic/stress costs, trade-level exit reasons, and tail capture.
It must not run a dense Peak threshold grid.

## Post-Run Attribution Gate

Every strategy research round that runs backtests must end with post-run attribution before it updates research memory, mature researcher queues, or the next experiment plan.

Prospective E23/E32-style monitors may update coverage and collection receipts,
but must keep outcomes unread until their preregistered sample gates pass.

The attribution gate is part of the same Agent, not a separate Agent. It must reuse the same knowledge graph, research memory, event-study evidence, backtest outputs, exported trades, and promotion blockers. Splitting attribution into a separate Agent is not allowed unless the workflow still treats the result as the same mandatory gate.

The gate must classify the result into explicit failure or edge buckets:

- signal edge: whether the entry event had forward-distribution expectancy before leverage;
- entry timing: whether MAE/MFE shows entries were late, early, or structurally adverse;
- exit quality: whether ROI, stoploss, time exits, or invalidation rules cut winners or held losers;
- cost and funding drag: whether fee, slippage, spread, or funding stress erased gross edge;
- fixed 50x risk amplification: whether the configured futures risk口径 magnified a weak signal rather than revealing edge;
- regime dependency: whether results depend on BTC lead, volatility, trend/range, funding, session, or pair-specific structure;
- sample validity: whether trade count, window coverage, and robustness are enough to justify another experiment.

No next experiment queue item may be created from a backtest round unless the attribution gate states what failed, what survived, and which single mechanism the next run is testing.

## Regime Window Gate

Regime labels must come from a reproducible BTC/ETH futures OHLCV manifest, not
from manually chosen example periods.

The Agent must build and read:

- `user_data/strategy_research/regime_windows/latest_regime_windows.json`
- `user_data/strategy_research/regime_windows/regime_inference_quarantine.json`

The manifest labels daily and window-level market states using BTC/ETH futures
returns, EMA structure, realized volatility, ATR%, BB width, trend strength,
range score, and BTC/ETH directional agreement. Regime matrix, event-study
context, family-risk gate, promotion gate, and strategy-family routing must use
this manifest.

Only windows with label share of at least `0.55` may be active. Each label may
publish one primary window plus up to two sufficiently independent validation
episodes. Lower-confidence candidates stay visible as
`insufficient_confidence` and cannot drive promotion. Family gates aggregate
all active home episodes instead of selecting the most profitable one.

Gate input must also be deterministic. A family/promotion gate may use an
explicit `--csv` or the SHA-256-locked
`user_data/strategy_research/reports/latest_experiment_source.json`; it must
never infer the intended experiment from file modification time.

Validation episodes for one family must be calendar-independent: selected home
episodes may be adjacent but must not overlap. Causal indicators are calculated
with history available at the decision timestamp, and a forward event-study
horizon must remain fully inside the selected regime window.

When a runner supplies execution-alignment targets in experiment provenance,
an explicit family-gate rerun must preserve that metadata. Post-run attribution
must consume targets and primary-cost backtest artifacts from the same
SHA-256-locked experiment source; stale mutable target files must not override
the registered experiment.

Legacy labels such as `bull_home`, `range_home`, `bear_home`, and
`high_vol_hostile` are quarantined. Old reports may still be used as raw
date-range backtest evidence, but their regime interpretation is
`needs_regime_relabel` until rebuilt against the manifest.

## Durable Rule

Do not describe the Agent as missing materials, knowledge, or self-iteration. Those already exist. The accurate framing is:

> The Agent already has materials, knowledge graph, and self-iteration. The next work is deeper integration: using the knowledge graph and durable memory to guide the existing automatic research loop.

The active research rule is:

> The current router controls deployment permission. The independent allocator selects the next under-covered research family and historical home regime. Knowledge proposes typed factors and events. Gross edge, realistic costs, and independent regime episodes test them. Only validated event candidates become strategy hypotheses. Backtests must then be attributed before memory or next experiments change.

The knowledge graph is a multi-domain external brain. It must not collapse back
to only trading-behavior or price-action notes. Normal research must load and
use these domains when relevant:

- price action
- regime routing
- derivatives structure: funding, OI, liquidation, mark/index basis
- microstructure and execution cost: spread, slippage, order book depth, minimum edge
- cross-asset lead-lag and shared market-factor context
- Freqtrade runtime execution hooks and config override checks

If a hypothesis depends on non-OHLCV features, required data coverage must be
verified before strategy synthesis. Missing funding/OI/L2/basis/router/runtime
data downgrades the idea to research-only diagnostics or event study; it cannot
become Freqtrade strategy code from theory alone.

## Factor Research Gate

Factor research is a required front-door stage inside the same Agent, not a
separate Agent. Its job is to stop the workflow from turning knowledge cards or
research memory directly into strategy classes.

The fixed sequence is:

1. The E1-E41 postmortem and independent allocator select an eligible under-covered family and data-derived home regime.
2. Knowledge graph and research memory propose typed mechanisms for that allocation.
3. Factor research scores `3m`, `5m`, and `15m` Binance USDT-M futures OHLCV
   factors against forward return, MFE, MAE, sample count, side, and timeframe.
   Conditions are observed on the completed signal candle and executed at the
   next candle open; the signal-candle close is not treated as a fill price.
4. Passing factor rows remain supporting evidence and are combined with a
   predeclared structural event for the allocated strategy family.
5. The composite event repeats the sample, gross-edge, realistic-cost,
   MFE/MAE, and independent-window gates with the factor threshold unchanged.
6. Any auxiliary input must have a causal Freqtrade runtime data path.
7. Only family-factor composite edge candidates may become concrete Freqtrade
   strategy classes, unless the run is explicitly labeled as a negative-control
   or redesign study.

The Agent must not say "external knowledge generated this strategy" unless the
factor/event evidence chain exists. External knowledge can inspire what to test;
the factor layer decides whether there is enough local market evidence to turn
it into a strategy hypothesis.

## Safety Boundary

This workflow is research-only. It must not start live trading, read exchange API keys, modify dry-run/live config, or promote a theory-derived strategy without evidence gates. It must not create PRs, comments, reviews, issues, or pushes against the official upstream `freqtrade/freqtrade` repository; that repository is read-only reference material for this Agent. Every `gh` CLI write/status-changing operation must run through `safe_gh_write.py`, explicitly bind `GH_REPO` to `AndyWang-forest/freqtrade`, and avoid direct `gh api` writes. Git commits may be pushed only through the verified personal `origin`, while upstream push remains disabled.

## Futures Runtime Safety Gate

For Binance USDT-M futures, a process heartbeat, UI `pong`, or Telegram startup
message is not enough to call dry-run or live review healthy. The Agent must
separate strategy evidence from runtime safety.

Every futures dry-run or live-review candidate must satisfy:

- The runtime config must allow ccxt to use the active VPN/proxy environment:
  `ccxt_config.requests_trust_env=true` and
  `ccxt_async_config.aiohttp_trust_env=true`.
- Startup must run a ccxt Binance futures preflight using the same Python
  environment as Freqtrade. It must fetch exchange time and at least two
  `BTC/USDT:USDT` 15m candles before the bot starts.
- Preflight failure blocks startup or promotion. A bot that is `RUNNING` but
  cannot fetch futures OHLCV is considered unsafe, not healthy.
- Startup must run the dry-run strategy risk preflight before the network
  preflight. It must verify that strategy hooks are callable through Freqtrade,
  config overrides are explicit, and the final effective values still satisfy
  the fixed futures risk contract.
- Exchange-side stoploss must be configured before live review:
  `order_types.stoploss=market`, `order_types.stoploss_on_exchange=true`, and
  futures `stoploss_price_type=mark`.
- The strategy risk preflight must block dry-run review if ROI, stoploss,
  50x leverage, 8h losing-trade custom exit, exchange-side stoploss, or the
  three-stoploss StoplossGuard cannot be loaded by Freqtrade.
- Live review requires a tiny-size operational test proving that a filled
  position receives an exchange-side stop order. Dry-run can validate config
  parsing, but it cannot prove the real exchange order exists.

This gate applies to all strategy families, not only A1. It exists because 50x
futures runtime failure can turn a network problem into an unmanaged position.
