#!/usr/bin/env python3
"""Build a local price-action knowledge layer for the strategy researcher.

The builder only stores public metadata, bounded public web snapshots, and
short original knowledge cards. It intentionally does not download paid books,
paid videos, or pirated PDFs.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from repo_paths import find_repo_root
from typing import Any


REPO_ROOT = find_repo_root()
AGENT_ROOT = REPO_ROOT / "user_data/strategy_research"
KNOWLEDGE_ROOT = AGENT_ROOT / "knowledge"
RAW_SOURCES = KNOWLEDGE_ROOT / "raw_sources"
SNAPSHOTS = RAW_SOURCES / "web_snapshots"
BILIBILI_DIR = RAW_SOURCES / "bilibili"
BOOKS_DIR = RAW_SOURCES / "books_to_add"
CARDS_DIR = KNOWLEDGE_ROOT / "knowledge_cards"
INDEX_DIR = KNOWLEDGE_ROOT / "index"
REPORT_MD = KNOWLEDGE_ROOT / "latest_price_action_knowledge_report.md"
REPORT_JSON = KNOWLEDGE_ROOT / "latest_price_action_knowledge_report.json"

BILIBILI_BVID = "BV1G2AgzkELu"
BILIBILI_VIEW_API = "https://api.bilibili.com/x/web-interface/view"
BILIBILI_PLAYER_API = "https://api.bilibili.com/x/player/v2"
MAX_SNAPSHOT_BYTES = 900_000


PUBLIC_WEB_SOURCES = [
    {
        "id": "brooks_trading_course_books",
        "title": "Al Brooks Price Action Trading Books",
        "url": "https://www.brookstradingcourse.com/price-action-trading-books/",
        "author": "Brooks Trading Course",
        "kind": "official_book_page",
        "license": "public webpage metadata; books are copyrighted",
    },
    {
        "id": "brooks_trading_course_home",
        "title": "Brooks Trading Course public overview",
        "url": "https://www.brookstradingcourse.com/",
        "author": "Brooks Trading Course",
        "kind": "official_course_page",
        "license": "public webpage metadata; paid course content is copyrighted",
    },
    {
        "id": "investopedia_price_action_intro",
        "title": "An Introduction to Price Action Trading Strategies",
        "url": "https://www.investopedia.com/articles/active-trading/110714/introduction-price-action-trading-strategies.asp",
        "author": "Investopedia",
        "kind": "web_article",
        "license": "public webpage snapshot for local research",
    },
    {
        "id": "investopedia_price_action_definition",
        "title": "Price Action: What It Is and How Stock Traders Use It",
        "url": "https://www.investopedia.com/terms/p/price-action.asp",
        "author": "Investopedia",
        "kind": "web_article",
        "license": "public webpage snapshot for local research",
    },
    {
        "id": "binance_academy_support_resistance",
        "title": "The Basics of Support and Resistance Explained",
        "url": "https://www.binance.com/en/academy/articles/the-basics-of-support-and-resistance-explained",
        "author": "Binance Academy",
        "kind": "crypto_web_article",
        "license": "public webpage snapshot for local research",
    },
    {
        "id": "coinmarketcap_support_resistance_zones",
        "title": "Technical Analysis 101: How to Find Support and Resistance Zones",
        "url": "https://coinmarketcap.com/academy/article/technical-analysis-101-how-to-find-support-and-resistance-zones",
        "author": "CoinMarketCap Academy",
        "kind": "crypto_web_article",
        "license": "public webpage snapshot for local research",
    },
    {
        "id": "kraken_technical_analysis_intro",
        "title": "A brief introduction to technical analysis",
        "url": "https://www.kraken.com/learn/introduction-to-technical-analysis",
        "author": "Kraken Learn",
        "kind": "crypto_web_article",
        "license": "public webpage snapshot for local research",
    },
    {
        "id": "phemex_crypto_price_action",
        "title": "How to Read Price Action in The Crypto Markets?",
        "url": "https://phemex.com/academy/crypto-price-action-trading",
        "author": "Phemex Academy",
        "kind": "crypto_web_article",
        "license": "public webpage snapshot for local research",
    },
    {
        "id": "binance_academy_funding_rates",
        "title": "What Are Funding Rates in Crypto Markets?",
        "url": "https://academy.binance.com/en/articles/what-are-funding-rates-in-crypto-markets",
        "author": "Binance Academy",
        "kind": "crypto_derivatives_article",
        "license": "public webpage snapshot for local research",
    },
    {
        "id": "binance_academy_liquidation",
        "title": "What Is Liquidation in Crypto Trading?",
        "url": "https://academy.binance.com/en/articles/what-is-liquidation-in-crypto-trading",
        "author": "Binance Academy",
        "kind": "crypto_derivatives_article",
        "license": "public webpage snapshot for local research",
    },
    {
        "id": "binance_academy_open_interest",
        "title": "What Is Open Interest in Futures Trading?",
        "url": "https://academy.binance.com/en/articles/what-is-open-interest-in-futures-trading",
        "author": "Binance Academy",
        "kind": "crypto_derivatives_article",
        "license": "public webpage snapshot for local research",
    },
    {
        "id": "binance_academy_order_book",
        "title": "What Is an Order Book?",
        "url": "https://academy.binance.com/en/articles/what-is-an-order-book",
        "author": "Binance Academy",
        "kind": "market_microstructure_article",
        "license": "public webpage snapshot for local research",
    },
    {
        "id": "binance_academy_bid_ask_spread",
        "title": "What Is the Bid-Ask Spread and Slippage?",
        "url": "https://academy.binance.com/en/articles/bid-ask-spread-and-slippage-explained",
        "author": "Binance Academy",
        "kind": "market_microstructure_article",
        "license": "public webpage snapshot for local research",
    },
]


STARTER_CARDS = [
    {
        "id": "pa_signal_bar_requires_context",
        "title": "信号K线必须放在背景里判断",
        "concepts": ["signal_bar", "context", "entry_confirmation"],
        "source_refs": ["bilibili_price_action_course", "brooks_trading_course_books"],
        "knowledge": "单根K线本身不是策略。先判断趋势、震荡、关键位置和最近买卖压力，再把信号K线当作入场触发器。",
        "strategy_hypothesis": "把方向判断和入场触发拆开：趋势/区间过滤只给方向，具体开仓必须等待1到2根短周期K线确认恢复或失败。",
        "freqtrade_translation": {
            "features": ["ema_slope", "range_position", "recent_swing_high_low", "signal_bar_body_ratio"],
            "entry_rules": ["方向过滤 + 信号K线 + 下一根K线确认，最多三个条件"],
            "avoid": ["只因出现锤子线/吞没线/大阳线就直接进场"],
        },
        "risk_notes": ["高杠杆下不要把信号K线的低点/高点机械当极近止损；要换算为杠杆后账户风险。"],
    },
    {
        "id": "pa_market_cycle_trend_range_transition",
        "title": "市场周期：趋势、震荡和过渡",
        "concepts": ["market_cycle", "trend", "trading_range", "regime"],
        "source_refs": ["bilibili_price_action_course", "investopedia_price_action_intro"],
        "knowledge": "策略必须先区分趋势行情、震荡行情和趋势转震荡的过渡段。同一入场形态在不同市场周期里含义不同。",
        "strategy_hypothesis": "Agent 生成策略时先选择行情类型，再选择策略族：趋势用回调恢复/突破延续，震荡用区间边界反转，过渡段降低频率或观望。",
        "freqtrade_translation": {
            "features": ["adx", "ema_spread", "atr_percentile", "range_width", "close_position_in_range"],
            "entry_rules": ["regime == trend 时禁用均值回归", "regime == range 时禁用追突破"],
            "avoid": ["同一套参数同时吃趋势和震荡"],
        },
        "risk_notes": ["行情切换是短周期策略回撤的高发区，必须保留冷却和最大回撤暂停。"],
    },
    {
        "id": "pa_breakout_needs_close_and_follow_through",
        "title": "突破需要收盘确认和后续跟进",
        "concepts": ["breakout", "follow_through", "false_breakout"],
        "source_refs": ["bilibili_price_action_course", "binance_academy_support_resistance"],
        "knowledge": "突破不是刺破价位。更可靠的突破通常需要收在关键位之外，并出现后续跟进K线或量能确认。",
        "strategy_hypothesis": "不要在触及阻力/支撑瞬间入场；测试收盘突破、回踩不破、或下一根继续推动三类确认。",
        "freqtrade_translation": {
            "features": ["prior_high_low", "close_break_distance", "volume_zscore", "followthrough_return"],
            "entry_rules": ["close > resistance + buffer", "next candle does not close back inside range"],
            "avoid": ["wick-only breakout", "breakout after stretched move without pullback"],
        },
        "risk_notes": ["假突破会快速反抽，高杠杆策略要限制追突破距离和滑点。"],
    },
    {
        "id": "pa_failed_breakout_as_reversal_seed",
        "title": "失败突破可以作为反向种子",
        "concepts": ["failed_breakout", "reversal", "trap"],
        "source_refs": ["bilibili_price_action_course", "coinmarketcap_support_resistance_zones"],
        "knowledge": "关键位突破后如果迅速收回区间，说明追突破的一方被套，反向移动可能更快。",
        "strategy_hypothesis": "设计假突破反转策略：先识别刺破关键位，再要求收回区间和反向小动量确认。",
        "freqtrade_translation": {
            "features": ["wick_outside_range", "close_back_inside_range", "reversal_body_ratio"],
            "entry_rules": ["刺破 + 收回 + 反向确认，不超过三个条件"],
            "avoid": ["在趋势极强时硬做反转"],
        },
        "risk_notes": ["反转策略必须有趋势过滤；强趋势里失败突破容易变成小回调。"],
    },
    {
        "id": "pa_pullback_resume_entry",
        "title": "趋势回调后的恢复入场",
        "concepts": ["pullback", "trend_resume", "entry_timing"],
        "source_refs": ["bilibili_price_action_course", "kraken_technical_analysis_intro"],
        "knowledge": "趋势策略不应一有方向就追。更好的位置通常是回调到动态支撑/压力附近，然后等待恢复。",
        "strategy_hypothesis": "用高一级时间框架定方向，低一级时间框架等待回调到均线/前低前高附近，再用恢复K线入场。",
        "freqtrade_translation": {
            "features": ["higher_tf_ema_slope", "pullback_to_ema", "resume_candle", "rsi_midzone"],
            "entry_rules": ["高周期方向 + 回调位置 + 恢复确认"],
            "avoid": ["离均线太远追单", "连续大K后追入"],
        },
        "risk_notes": ["回调策略交易次数会少，必须用多窗口样本验证，不要凭一段行情晋级。"],
    },
    {
        "id": "pa_support_resistance_are_zones",
        "title": "支撑阻力是区域，不是一根线",
        "concepts": ["support", "resistance", "zone", "liquidity"],
        "source_refs": ["binance_academy_support_resistance", "coinmarketcap_support_resistance_zones", "kraken_technical_analysis_intro"],
        "knowledge": "支撑阻力应被视为价格区域。短周期噪音和交易所盘口会导致针刺，机械按单一价格判断容易被洗掉。",
        "strategy_hypothesis": "把关键位转换成ATR或近期波动率宽度的区域，再测试区域内反应，而不是点位触发。",
        "freqtrade_translation": {
            "features": ["atr_zone_width", "swing_cluster", "touch_count", "rejection_from_zone"],
            "entry_rules": ["进入区域 + 拒绝形态 + 反向确认"],
            "avoid": ["一触线就买卖"],
        },
        "risk_notes": ["止损也要放在区域外并考虑杠杆后亏损，不能只看裸价格距离。"],
    },
    {
        "id": "pa_order_type_changes_strategy_meaning",
        "title": "订单类型会改变策略含义",
        "concepts": ["stop_order", "limit_order", "execution", "slippage"],
        "source_refs": ["bilibili_price_action_course"],
        "knowledge": "Stop order 更像突破/动量确认，limit order 更像回调/均值回归。信号相同但订单类型不同，策略实际暴露不同。",
        "strategy_hypothesis": "Freqtrade 回测里虽然不是盘口级撮合，也要把策略意图写清楚：追随突破、回调挂单、还是反转接刀。",
        "freqtrade_translation": {
            "features": ["entry_type_tag", "expected_slippage_bps", "spread_proxy"],
            "entry_rules": ["动量策略用确认后进场", "均值策略用区域反应后进场"],
            "avoid": ["用市价追所有信号"],
        },
        "risk_notes": ["短周期合约策略手续费和滑点可能吃掉大部分 edge，必须做压力测试。"],
    },
    {
        "id": "pa_crypto_needs_fee_and_24h_regime_adjustment",
        "title": "加密货币价格行为要额外处理手续费和24小时 regime",
        "concepts": ["crypto", "fees", "funding", "sessionless_market"],
        "source_refs": ["phemex_crypto_price_action", "investopedia_price_action_definition"],
        "knowledge": "传统价格行为多来自股票/期货盘中经验，加密货币是24小时交易，波动聚集、资金费率、周末流动性和交易所差异都会影响形态可靠性。",
        "strategy_hypothesis": "每个价格行为策略都必须加手续费/滑点压力测试、时间段切片和 BTC/ETH 分资产验证。",
        "freqtrade_translation": {
            "features": ["hour_of_day", "day_of_week", "funding_window", "fee_stress"],
            "entry_rules": ["形态策略必须跨时间段验证", "费用压力下PF仍需过线"],
            "avoid": ["把股票盘中形态不加验证地搬到50x合约"],
        },
        "risk_notes": ["如果裸信号收益接近0，高杠杆只会放大噪音和费用，不会创造 edge。"],
    },
    {
        "id": "ms_derivatives_funding_bias_is_context_not_signal",
        "title": "资金费率是拥挤度背景，不是单独入场信号",
        "knowledge_domain": "derivatives",
        "category": "crypto_derivatives",
        "concepts": ["funding_rate", "crowding", "basis", "derivatives_context", "regime_router"],
        "source_refs": ["binance_academy_funding_rates"],
        "knowledge": "永续合约资金费率反映多空持仓成本和拥挤方向。极端 funding 可以解释反向挤压风险，但单独使用容易变成追拥挤交易。",
        "strategy_hypothesis": "把 funding 作为策略族开关或风险降档变量：极端正 funding 下谨慎追多，极端负 funding 下谨慎追空；只有与价格结构、波动扩张和 BTC lead 同向时才允许入场。",
        "freqtrade_translation": {
            "strategy_family": "volatility_compression_directional_expansion",
            "features": ["funding_rate_8h", "funding_zscore_7d", "funding_sign", "mark_index_basis", "price_momentum_confirm"],
            "entry_rules": ["funding 不作为直接触发，只作为拥挤过滤", "方向信号必须由 3m/5m/15m 价格结构触发"],
            "exit_rules": ["funding 极端反向扩大且价格未继续推动时降低持仓时间"],
            "applicable_regimes": ["high_volatility_expansion", "trend_continuation"],
            "not_applicable_regimes": ["range_chop"],
        },
        "risk_notes": ["funding 数据缺失时不得假定为0；报告必须标记 coverage。"],
        "data_requirements": ["funding_rate", "mark_price", "index_price"],
        "avoid_rules": ["不要把 funding 正负直接翻译成做多/做空。"],
    },
    {
        "id": "ms_regime_router_is_strategy_family_selector",
        "title": "Regime router 先决定策略族，不是事后解释标签",
        "knowledge_domain": "regime",
        "category": "regime_router",
        "concepts": ["regime_router", "home_regime", "hostile_regime", "no_trade", "strategy_family"],
        "source_refs": ["phemex_crypto_price_action", "investopedia_price_action_definition"],
        "knowledge": "高杠杆合约策略不应被要求在所有行情里都赚钱。先用数据标注当前市场状态，再选择对应策略族；没有匹配策略族时，no-trade 是有效决策。",
        "strategy_hypothesis": "每轮策略研究前先读取 regime manifest 和 current-market router，只在 home regime 内验证策略族 edge，在 hostile regime 内验证风控兜底和暂停机制。",
        "freqtrade_translation": {
            "strategy_family": "market_state_router",
            "features": ["regime_label", "trend_score", "volatility_percentile", "range_score", "btc_eth_direction_agreement"],
            "entry_rules": ["router 允许策略族后，策略自身仍必须出现 3m/5m/15m 入场触发", "router 输出 no-trade 时不生成新交易策略"],
            "exit_rules": ["regime flip against family 时缩短持仓或暂停新开仓"],
            "applicable_regimes": ["all_regimes"],
            "not_applicable_regimes": [],
        },
        "risk_notes": ["旧手工 bull/range/bear/high_vol 标签不能作为新经验依据；必须挂到数据生成的 manifest 证据。"],
        "data_requirements": ["regime_manifest", "ohlcv", "current_market_router"],
        "avoid_rules": ["不要把策略在错误窗口亏损简单解释为策略无效；先确认窗口是否属于该策略族 home regime。"],
    },
    {
        "id": "ms_open_interest_confirms_participation",
        "title": "OI 用来判断突破是否有持仓参与",
        "knowledge_domain": "derivatives",
        "category": "crypto_derivatives",
        "concepts": ["open_interest", "participation", "breakout_quality", "leverage_crowding"],
        "source_refs": ["binance_academy_open_interest"],
        "knowledge": "Open interest 增加说明新杠杆仓位进入市场，和价格方向结合后可区分新趋势参与、空头回补、或多头被动止损。",
        "strategy_hypothesis": "突破/扩张策略必须区分价格移动是否伴随 OI 扩张。价格突破但 OI 不增，可能只是流动性缺口或回补，不应升级为强趋势信号。",
        "freqtrade_translation": {
            "strategy_family": "high_volatility_breakout_continuation",
            "features": ["oi_change_1h", "oi_change_4h", "price_return_1h", "volume_ratio", "atr_expansion"],
            "entry_rules": ["价格扩张 + OI 同向参与 + 下一根确认", "OI 缺失时只允许 research-only 降级实验"],
            "exit_rules": ["OI 快速回落且价格未延续时 time-stop"],
            "applicable_regimes": ["high_volatility_expansion", "trend_continuation"],
            "not_applicable_regimes": ["low_volatility_range"],
        },
        "risk_notes": ["OI 是交易所级数据，覆盖不足时不能进入 promotion gate。"],
        "data_requirements": ["open_interest", "volume", "ohlcv"],
        "avoid_rules": ["不要把单根大K当作有参与的突破。"],
    },
    {
        "id": "ms_liquidation_risk_turns_breakout_into_reversal",
        "title": "清算区附近的突破可能是延续也可能是反转",
        "knowledge_domain": "derivatives",
        "category": "crypto_derivatives",
        "concepts": ["liquidation", "stop_run", "forced_flow", "false_breakout", "risk_event"],
        "source_refs": ["binance_academy_liquidation"],
        "knowledge": "高杠杆市场中，价格穿过拥挤清算区会产生强制流。强制流可以推动延续，也可能在流动性吃完后快速反转。",
        "strategy_hypothesis": "把清算/止损扫荡结构拆成两类事件：扫后继续放量收在区外做延续；扫后快速收回区间做失败突破反转。",
        "freqtrade_translation": {
            "strategy_family": "range_false_break_reversion",
            "features": ["wick_outside_range", "close_back_inside_range", "volume_spike", "liquidation_proxy", "next_candle_confirm"],
            "entry_rules": ["扫区间边界 + 收回 + 下一根确认才可反向", "扫后收在区外且 ATR 扩张才可顺势"],
            "exit_rules": ["反向策略必须在 4h 内回到 box_mid，否则 time-stop"],
            "applicable_regimes": ["range_chop", "high_volatility_reversal"],
            "not_applicable_regimes": ["clean_trend_continuation"],
        },
        "risk_notes": ["没有真实清算数据时只能用 wick/volume/ATR proxy，结论必须标为 proxy。"],
        "data_requirements": ["ohlcv", "liquidation_proxy_optional", "volume"],
        "avoid_rules": ["不要在强趋势中机械做所有刺破反转。"],
    },
    {
        "id": "ms_microstructure_spread_slippage_sets_minimum_edge",
        "title": "盘口价差和滑点决定最小可交易 edge",
        "knowledge_domain": "microstructure",
        "category": "execution_cost",
        "concepts": ["order_book", "spread", "slippage", "minimum_edge", "execution_quality"],
        "source_refs": ["binance_academy_order_book", "binance_academy_bid_ask_spread"],
        "knowledge": "短周期策略的可交易性先由价差、滑点、成交深度和订单类型决定。K线事件的平均收益如果接近成本，50x 只会放大成本噪音。",
        "strategy_hypothesis": "每个 3m/5m/15m 策略族都必须先通过 realistic cost，再通过 stress cost；低于最小边际收益的事件不能写成 Freqtrade class。",
        "freqtrade_translation": {
            "strategy_family": "no_trade_capital_protection",
            "features": ["spread_proxy", "volume_ratio", "atr_pct", "expected_move_vs_cost", "order_type_tag"],
            "entry_rules": ["expected_move_vs_cost 必须大于阈值", "低波动低成交时 no-trade"],
            "exit_rules": ["MFE 未覆盖成本时缩短持仓"],
            "applicable_regimes": ["all_regimes"],
            "not_applicable_regimes": [],
        },
        "risk_notes": ["没有 L2 时必须使用保守 slippage proxy；不要把 event study 裸收益当真实收益。"],
        "data_requirements": ["ohlcv", "fee_model", "slippage_model", "optional_l2_order_book"],
        "avoid_rules": ["不要为了增加交易数降低成本门槛。"],
    },
    {
        "id": "ms_cross_asset_lead_lag_requires_event_alignment",
        "title": "跨币种 lead-lag 必须验证事件对齐",
        "knowledge_domain": "cross_asset",
        "category": "cross_asset_structure",
        "concepts": ["btc_lead", "eth_beta", "sol_beta", "lead_lag", "market_factor"],
        "source_refs": ["phemex_crypto_price_action", "binance_academy_open_interest"],
        "knowledge": "高流动性加密合约经常共享市场因子。BTC 的方向、波动和风险偏好会影响 ETH/SOL/BNB/XRP，但 lead-lag 不是固定常数。",
        "strategy_hypothesis": "跨币种策略先做 event alignment：BTC/ETH 背景只作为许可层，目标币必须自己出现入场触发；禁止只因 BTC 动了就交易 alt。",
        "freqtrade_translation": {
            "strategy_family": "cross_asset_lead_lag",
            "features": ["btc_ret_1h", "btc_volatility_state", "target_ret_15m", "target_beta_30d", "direction_agreement"],
            "entry_rules": ["BTC/ETH 背景许可 + 目标币自身触发 + 下一根确认", "lead 信号必须在训练外窗口验证"],
            "exit_rules": ["BTC lead 反向且目标币未延续时退出"],
            "applicable_regimes": ["trend_continuation", "high_volatility_expansion"],
            "not_applicable_regimes": ["range_chop"],
        },
        "risk_notes": ["跨币种扩展 pairs 只作为研究泛化证据，不自动进入 registry/dry-run。"],
        "data_requirements": ["ohlcv_research_all", "pair_universe", "rolling_beta"],
        "avoid_rules": ["不要把相关性当因果。"],
    },
    {
        "id": "ms_execution_hooks_must_match_freqtrade_runtime",
        "title": "风控必须落到 Freqtrade 正确钩子",
        "knowledge_domain": "execution",
        "category": "runtime_execution",
        "concepts": ["freqtrade_hooks", "custom_exit", "protections", "runtime_override", "dryrun_preflight"],
        "source_refs": ["phemex_crypto_price_action"],
        "knowledge": "策略代码里的风控只有写进 Freqtrade 会调用的接口才会生效；配置层保护、策略层 custom_exit、订单层 stoploss_on_exchange 各自作用不同。",
        "strategy_hypothesis": "每个进入 dry-run review 的策略必须先通过 runtime risk preflight，列出策略参数、配置覆盖、custom_exit 是否 callable、protections 是否按预期启用。",
        "freqtrade_translation": {
            "strategy_family": "no_trade_capital_protection",
            "features": ["custom_exit_present", "leverage_method_returns_50", "effective_roi", "effective_stoploss", "protections_config"],
            "entry_rules": ["策略生成前声明 runtime assumptions", "dry-run 前运行 dryrun_strategy_risk_preflight"],
            "exit_rules": ["time-stop、peak-drawdown、stoploss guard 必须能被 Freqtrade 调用"],
            "applicable_regimes": ["all_regimes"],
            "not_applicable_regimes": [],
        },
        "risk_notes": ["配置覆盖策略字段时必须在报告里说明最终生效值。"],
        "data_requirements": ["freqtrade_strategy_load", "effective_config_dump"],
        "avoid_rules": ["不要写 Freqtrade 不会调用的伪风控函数。"],
    },
]


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def request_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    full_url = url
    if params:
        full_url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(full_url, headers={"User-Agent": "Mozilla/5.0 local-strategy-researcher/0.1"})
    with urllib.request.urlopen(request, timeout=25) as response:  # noqa: S310 - user-requested bounded fetch.
        return json.loads(response.read().decode("utf-8"))


def fetch_bytes(url: str) -> tuple[bytes, str, bool, str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 local-strategy-researcher/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:  # noqa: S310 - user-requested bounded fetch.
            raw = response.read(MAX_SNAPSHOT_BYTES + 1)
            return raw[:MAX_SNAPSHOT_BYTES], response.headers.get("content-type", "unknown"), len(raw) > MAX_SNAPSHOT_BYTES, None
    except (urllib.error.URLError, TimeoutError) as exc:
        fallback = subprocess.run(
            [
                "curl",
                "-L",
                "--max-time",
                "25",
                "-A",
                "Mozilla/5.0 local-strategy-researcher/0.1",
                url,
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        raw = fallback.stdout[:MAX_SNAPSHOT_BYTES]
        if raw:
            return raw, "unknown", len(fallback.stdout) > MAX_SNAPSHOT_BYTES, f"urllib_failed_then_curl_ok: {exc}"
        return b"", "unknown", False, f"{exc}; curl_stderr={fallback.stderr.decode('utf-8', errors='replace')[:500]}"


def html_to_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_bilibili_manifest() -> dict[str, Any]:
    view = request_json(BILIBILI_VIEW_API, {"bvid": BILIBILI_BVID})
    if view.get("code") != 0:
        raise RuntimeError(f"Bilibili view API failed: {view.get('code')} {view.get('message')}")
    data = view["data"]
    pages = data.get("pages") or []
    subtitle_status = []
    for page in pages:
        player = request_json(BILIBILI_PLAYER_API, {"bvid": BILIBILI_BVID, "cid": page["cid"]})
        player_data = player.get("data") or {}
        subtitles = (player_data.get("subtitle") or {}).get("subtitles") or []
        subtitle_status.append(
            {
                "page": page.get("page"),
                "cid": page.get("cid"),
                "part": page.get("part"),
                "need_login_subtitle": bool(player_data.get("need_login_subtitle")),
                "subtitle_count": len(subtitles),
                "subtitle_urls": [item.get("subtitle_url") for item in subtitles if item.get("subtitle_url")],
            }
        )
    manifest = {
        "id": "bilibili_price_action_course",
        "fetched_at_utc": now_utc(),
        "source_type": "bilibili_multi_part_video",
        "url": f"https://www.bilibili.com/video/{BILIBILI_BVID}/",
        "bvid": BILIBILI_BVID,
        "title": data.get("title"),
        "owner": (data.get("owner") or {}).get("name"),
        "aid": data.get("aid"),
        "page_count": len(pages),
        "duration_seconds": data.get("duration"),
        "rights": data.get("rights"),
        "subtitle_access": {
            "public_subtitle_pages": sum(1 for item in subtitle_status if item["subtitle_count"] > 0),
            "login_required_pages": sum(1 for item in subtitle_status if item["need_login_subtitle"]),
            "note": "Only public subtitle metadata is fetched. Add legally obtained transcripts under raw_sources/bilibili/transcripts/ if needed.",
        },
        "pages": pages,
        "subtitle_status": subtitle_status,
        "copyright_policy": {
            "stored": ["metadata", "page list", "public subtitle metadata if available"],
            "not_stored": ["video files", "paid content", "full copyrighted transcripts unless provided locally with permission"],
        },
    }
    write_json(BILIBILI_DIR / "bilibili_price_action_course_manifest.json", manifest)
    return manifest


def fetch_public_sources() -> list[dict[str, Any]]:
    results = []
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    for source in PUBLIC_WEB_SOURCES:
        raw, content_type, truncated, error = fetch_bytes(source["url"])
        snapshot_path = SNAPSHOTS / f"{source['id']}.snapshot"
        text_path = SNAPSHOTS / f"{source['id']}.txt"
        if raw:
            snapshot_path.write_bytes(raw)
            text_path.write_text(html_to_text(raw)[:120_000] + "\n", encoding="utf-8")
        results.append(
            {
                **source,
                "fetched_at_utc": now_utc(),
                "fetch_status": "ok" if raw else "failed",
                "error": error if error else (None if raw else "empty_response"),
                "snapshot": rel(snapshot_path) if raw else None,
                "text_extract": rel(text_path) if raw else None,
                "bytes": len(raw),
                "content_type": content_type,
                "truncated": truncated,
            }
        )
    write_json(RAW_SOURCES / "public_web_sources_manifest.json", {"sources": results})
    return results


def build_books_manifest() -> dict[str, Any]:
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": "al_brooks_books_manual_import",
        "updated_at_utc": now_utc(),
        "status": "waiting_for_licensed_local_files",
        "books": [
            {
                "title": "Trading Price Action Trends",
                "author": "Al Brooks",
                "publisher": "Wiley",
                "status": "copyrighted_not_downloaded",
            },
            {
                "title": "Trading Price Action Trading Ranges",
                "author": "Al Brooks",
                "publisher": "Wiley",
                "status": "copyrighted_not_downloaded",
            },
            {
                "title": "Trading Price Action Reversals",
                "author": "Al Brooks",
                "publisher": "Wiley",
                "status": "copyrighted_not_downloaded",
            },
            {
                "title": "Reading Price Charts Bar by Bar",
                "author": "Al Brooks",
                "publisher": "Wiley",
                "status": "copyrighted_not_downloaded",
            },
        ],
        "local_import_instruction": "Put legally owned PDFs/TXT/EPUB exports here, then summarize into short knowledge cards. Do not commit book files.",
    }
    write_json(BOOKS_DIR / "al_brooks_books_manifest.json", manifest)
    readme = """# Books To Add

这里放你合法拥有的书籍文本/PDF/EPUB 导出，例如 Al Brooks 的 Wiley 价格行为学书。

当前脚本不会从非官方 PDF 站下载书籍，也不会把整本书内容提交进仓库。建议流程：

1. 把你自己拥有的文件放到本目录。
2. 后续只抽取短知识卡、概念、量化规则和来源定位。
3. 不把大段原文、整章翻译或盗版 PDF 写入 Git。
"""
    (BOOKS_DIR / "README.md").write_text(readme, encoding="utf-8")
    return manifest


def write_cards() -> list[dict[str, Any]]:
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    cards = []
    for card in STARTER_CARDS:
        translation = dict(card.get("freqtrade_translation") or {})
        translation.setdefault("strategy_family", "market_state_router" if card.get("knowledge_domain") == "regime" else "price_action_research")
        translation.setdefault("features", [])
        translation.setdefault("entry_rules", [])
        translation.setdefault("exit_rules", [])
        translation.setdefault("applicable_regimes", [])
        translation.setdefault("not_applicable_regimes", [])
        payload = {
            **card,
            "freqtrade_translation": translation,
            "version": 1,
            "created_at_utc": now_utc(),
            "copyright_note": "Original local summary for strategy research. Not a verbatim excerpt.",
            "category": card.get("category", "price_action"),
            "knowledge_domain": card.get("knowledge_domain", "price_action"),
            "data_requirements": card.get("data_requirements", ["ohlcv"]),
            "source_quality": {
                "level": "medium",
                "usable_transcript_count": 0,
                "web_source_count": len(card.get("source_refs", [])),
                "book_source_count": 0,
                "note": "Versioned starter card; source refs are bounded public metadata or user-approved local references.",
            },
            "verification_status": {
                "state": "knowledge_only_requires_backtest",
                "required_checks": [
                    "required_data_coverage_check",
                    "factor_research",
                    "event_study_edge_check",
                    "freqtrade_backtesting",
                    "recursive_analysis",
                    "lookahead_analysis",
                    "regime_matrix",
                    "fee_slippage_stress",
                    "promotion_gate",
                ],
                "quarantined": False,
            },
            "agent_use": {
                "when_to_retrieve": card["concepts"],
                "must_turn_into_testable_rule": True,
                "must_backtest_before_candidate": True,
                "must_verify_required_data": bool(card.get("data_requirements")),
            },
        }
        write_json(CARDS_DIR / f"{card['id']}.json", payload)
        cards.append(payload)
    return cards


def build_index(cards: list[dict[str, Any]], bilibili: dict[str, Any], web_sources: list[dict[str, Any]], books: dict[str, Any]) -> dict[str, Any]:
    concept_index: dict[str, list[str]] = {}
    domain_index: dict[str, list[str]] = {}
    data_requirement_index: dict[str, list[str]] = {}
    for card in cards:
        for concept in card["concepts"]:
            concept_index.setdefault(concept, []).append(card["id"])
        domain_index.setdefault(card.get("knowledge_domain", "price_action"), []).append(card["id"])
        for requirement in card.get("data_requirements", ["ohlcv"]):
            data_requirement_index.setdefault(requirement, []).append(card["id"])
    index = {
        "generated_at_utc": now_utc(),
        "knowledge_root": rel(KNOWLEDGE_ROOT),
        "card_count": len(cards),
        "concept_index": concept_index,
        "domain_index": domain_index,
        "data_requirement_index": data_requirement_index,
        "sources": {
            "bilibili": {
                "title": bilibili.get("title"),
                "page_count": bilibili.get("page_count"),
                "manifest": rel(BILIBILI_DIR / "bilibili_price_action_course_manifest.json"),
            },
            "public_web": [
                {
                    "id": item["id"],
                    "title": item["title"],
                    "url": item["url"],
                    "fetch_status": item["fetch_status"],
                    "text_extract": item["text_extract"],
                }
                for item in web_sources
            ],
            "books": {
                "status": books["status"],
                "manifest": rel(BOOKS_DIR / "al_brooks_books_manifest.json"),
            },
        },
    }
    write_json(INDEX_DIR / "price_action_knowledge_index.json", index)
    return index


def write_report(index: dict[str, Any], bilibili: dict[str, Any], web_sources: list[dict[str, Any]], books: dict[str, Any]) -> None:
    lines = [
        "# Price Action Knowledge Base",
        "",
        f"- Generated UTC: `{index['generated_at_utc']}`",
        f"- Knowledge root: `{index['knowledge_root']}`",
        f"- Knowledge cards: `{index['card_count']}`",
        "",
        "## Bilibili Course",
        "",
        f"- Title: `{bilibili.get('title')}`",
        f"- Owner: `{bilibili.get('owner')}`",
        f"- Pages: `{bilibili.get('page_count')}`",
        f"- Duration seconds: `{bilibili.get('duration_seconds')}`",
        f"- Public subtitle pages: `{bilibili['subtitle_access']['public_subtitle_pages']}`",
        f"- Login-required subtitle pages: `{bilibili['subtitle_access']['login_required_pages']}`",
        f"- Manifest: `{index['sources']['bilibili']['manifest']}`",
        "",
        "## Public Web Sources",
        "",
        "| Source | Status | Bytes | Text Extract |",
        "|---|---|---:|---|",
    ]
    for item in web_sources:
        lines.append(f"| {item['id']} | {item['fetch_status']} | {item['bytes']} | `{item['text_extract']}` |")
    lines.extend(
        [
            "",
            "## Books",
            "",
            f"- Status: `{books['status']}`",
            f"- Manifest: `{rel(BOOKS_DIR / 'al_brooks_books_manifest.json')}`",
            "- Al Brooks/Wiley books are copyrighted; this builder records bibliographic targets and waits for legally owned local files.",
            "",
            "## Concept Index",
            "",
            "| Concept | Cards |",
            "|---|---|",
        ]
    )
    for concept, card_ids in sorted(index["concept_index"].items()):
        lines.append(f"| {concept} | {', '.join(card_ids)} |")
    lines.extend(
        [
            "",
            "## Knowledge Domains",
            "",
            "| Domain | Cards |",
            "|---|---|",
        ]
    )
    for domain, card_ids in sorted(index["domain_index"].items()):
        lines.append(f"| {domain} | {', '.join(card_ids)} |")
    lines.extend(
        [
            "",
            "## Data Requirements",
            "",
            "| Requirement | Cards |",
            "|---|---|",
        ]
    )
    for requirement, card_ids in sorted(index["data_requirement_index"].items()):
        lines.append(f"| {requirement} | {', '.join(card_ids)} |")
    lines.extend(
        [
            "",
            "## Agent Usage",
            "",
            "1. Query cards before generating a strategy hypothesis.",
            "2. Retrieve both price-action and market-structure cards when a strategy family depends on futures mechanics.",
            "3. Convert at most 1-3 retrieved concepts into testable Freqtrade rules.",
            "4. Verify required data coverage before strategy synthesis; missing data downgrades the idea to research-only.",
            "5. Backtest and write the result into research memory before reusing the idea.",
        ]
    )
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(REPORT_JSON, {"index": index, "report": rel(REPORT_MD)})


def main() -> None:
    for path in [KNOWLEDGE_ROOT, RAW_SOURCES, SNAPSHOTS, BILIBILI_DIR, CARDS_DIR, INDEX_DIR]:
        path.mkdir(parents=True, exist_ok=True)
    bilibili = build_bilibili_manifest()
    web_sources = fetch_public_sources()
    books = build_books_manifest()
    cards = write_cards()
    index = build_index(cards, bilibili, web_sources, books)
    write_report(index, bilibili, web_sources, books)
    print(f"Wrote {rel(REPORT_MD)}")
    print(f"Wrote {rel(INDEX_DIR / 'price_action_knowledge_index.json')}")


if __name__ == "__main__":
    main()
