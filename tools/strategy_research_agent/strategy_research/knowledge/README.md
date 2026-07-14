# Strategy Agent Knowledge Cards

This directory contains versioned, short-form knowledge cards for the local
strategy research Agent.

## What Is Versioned

- `knowledge_cards/`: active cards that can guide research hypotheses.
- `knowledge_cards_quarantined/`: cards retained as reviewed reference material but excluded from active hypothesis generation.

Cards are intentionally short. They contain concepts, source references, a
testable strategy hypothesis, Freqtrade translation hints, risk notes, and avoid
rules.

## What Is Not Versioned

Do not commit:

- raw Bilibili subtitles
- downloaded videos
- PDFs or book files
- full web snapshots
- browser cookies
- generated graph/report/dashboard/backtest artifacts

Those belong in local `user_data/strategy_research/knowledge/raw_sources/` and
other runtime directories.

## Quarantined Market-Structure Domains

`market_structure_chan` is currently a hypothesis-only domain. Its cards record
confirmation time, signal availability, repaint risk, structural level, and a
Freqtrade translation, but they cannot authorize strategy generation or become
durable research experience. The first admissible evidence is the causal 15m
third-point event study against a simple Donchian breakout/retest baseline:

```bash
user_data/strategy_research/start_manual_research.sh --chan-event-study
```

Even a positive event study leaves the cards quarantined pending manual review,
lookahead-analysis, recursive-analysis, full Freqtrade backtesting, family risk,
and promotion gates.

## Runtime Build

After installing the agent runtime, rebuild the usable knowledge layer:

```bash
user_data/strategy_research/start_manual_research.sh --agent-brain
```

For the weekly external update loop:

```bash
user_data/strategy_research/start_manual_research.sh --weekly-knowledge-update
```
