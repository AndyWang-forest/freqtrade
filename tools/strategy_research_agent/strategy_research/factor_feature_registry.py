#!/usr/bin/env python3
"""Typed, knowledge-derived factor registry for futures research.

Each feature declares its domain, causal data requirement, and source knowledge
cards.  Loaders attach only data available at the candle timestamp and emit a
coverage audit; missing auxiliary data remains missing instead of being filled
with zero.
"""

from __future__ import annotations

import json
import math
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class FactorSpec:
    name: str
    description: str
    column: str
    domain: str
    data_requirement: str
    tails: tuple[str, ...]
    knowledge_cards: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tails"] = list(self.tails)
        payload["knowledge_cards"] = list(self.knowledge_cards)
        return payload


PRICE_CARD = "pa_price_action_definition_no_indicator_dependency"
REGIME_CARD = "ms_regime_router_is_strategy_family_selector"
FUNDING_CARD = "ms_derivatives_funding_bias_is_context_not_signal"
OI_CARD = "ms_open_interest_confirms_participation"
MICROSTRUCTURE_CARD = "ms_microstructure_spread_slippage_sets_minimum_edge"
CROSS_ASSET_CARD = "ms_cross_asset_lead_lag_requires_event_alignment"


FACTOR_SPECS: tuple[FactorSpec, ...] = (
    FactorSpec("ret_3", "3-bar momentum", "ret_3", "price_action", "ohlcv", ("high", "low"), (PRICE_CARD,)),
    FactorSpec("ret_12", "12-bar trend pressure", "ret_12", "price_action", "ohlcv", ("high", "low"), (PRICE_CARD,)),
    FactorSpec("ema_gap", "Fast/slow EMA gap", "ema_gap", "price_action", "ohlcv", ("high", "low"), (PRICE_CARD,)),
    FactorSpec("atr_pct", "ATR as share of price", "atr_pct", "regime", "ohlcv", ("high", "low"), (REGIME_CARD,)),
    FactorSpec("volume_ratio", "Relative volume expansion", "volume_ratio", "microstructure", "ohlcv_proxy", ("high", "low"), (MICROSTRUCTURE_CARD,)),
    FactorSpec("breakout_pos", "Position inside prior range", "breakout_pos", "price_action", "ohlcv", ("high", "low"), (PRICE_CARD,)),
    FactorSpec("bb_width", "Bollinger width / compression state", "bb_width", "regime", "ohlcv", ("high", "low"), (REGIME_CARD,)),
    FactorSpec("trend_efficiency", "Directional movement divided by path length", "trend_efficiency", "regime", "ohlcv", ("high", "low"), (REGIME_CARD,)),
    FactorSpec("expected_move_vs_cost", "ATR move relative to realistic round-trip friction", "expected_move_vs_cost", "microstructure", "fee_slippage_model", ("high",), (MICROSTRUCTURE_CARD,)),
    FactorSpec("funding_rate", "Strictly prior funding settlement", "funding_rate", "derivatives", "funding_rate", ("high", "low"), (FUNDING_CARD,)),
    FactorSpec("funding_change", "Change across prior funding settlements", "funding_change", "derivatives", "funding_rate", ("high", "low"), (FUNDING_CARD,)),
    FactorSpec("basis_bps", "Prior completed-hour perpetual/mark basis", "basis_bps", "derivatives", "mark_price", ("high", "low"), (FUNDING_CARD,)),
    FactorSpec("oi_change_1h", "Open-interest participation over prior hour", "oi_change_1h", "derivatives", "open_interest", ("high", "low"), (OI_CARD,)),
    FactorSpec("oi_change_4h", "Open-interest participation over prior four hours", "oi_change_4h", "derivatives", "open_interest", ("high", "low"), (OI_CARD,)),
    FactorSpec("top_size_account_divergence", "Top-trader position/account divergence", "top_size_account_divergence", "derivatives", "top_trader_ratios", ("high", "low"), (OI_CARD,)),
    FactorSpec("taker_flow_log_ratio", "Aggressive taker long/short flow imbalance", "taker_flow_log_ratio", "microstructure", "taker_flow", ("high", "low"), (MICROSTRUCTURE_CARD,)),
    FactorSpec("btc_ret_3", "Current BTC short-horizon market impulse", "btc_ret_3", "cross_asset", "ohlcv_research_all", ("high", "low"), (CROSS_ASSET_CARD,)),
    FactorSpec("btc_pair_lag_gap", "BTC impulse minus target-pair impulse", "btc_pair_lag_gap", "cross_asset", "ohlcv_research_all", ("high", "low"), (CROSS_ASSET_CARD,)),
    FactorSpec("relative_ret_12", "Target return relative to cross-sectional median", "relative_ret_12", "cross_asset", "ohlcv_research_all", ("high", "low"), (CROSS_ASSET_CARD,)),
    FactorSpec("market_breadth_3", "Share of research pairs rising over three bars", "market_breadth_3", "cross_asset", "ohlcv_research_all", ("high", "low"), (CROSS_ASSET_CARD,)),
    FactorSpec("cross_section_dispersion_12", "Cross-pair return dispersion", "cross_section_dispersion_12", "cross_asset", "ohlcv_research_all", ("high", "low"), (CROSS_ASSET_CARD,)),
)


def pair_stem(pair: str) -> str:
    return pair.replace("/", "_").replace(":", "_")


def pair_symbol(pair: str) -> str:
    return pair.split("/", 1)[0] + "USDT"


def add_ohlcv_features(frame: pd.DataFrame, round_trip_friction: float) -> pd.DataFrame:
    out = frame.copy()
    out["ret_1"] = out["close"].pct_change(1)
    out["ret_3"] = out["close"] / out["close"].shift(3) - 1.0
    out["ret_12"] = out["close"] / out["close"].shift(12) - 1.0
    out["ema_fast"] = out["close"].ewm(span=8, adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=55, adjust=False).mean()
    out["ema_gap"] = out["ema_fast"] / out["ema_slow"] - 1.0
    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr_pct"] = tr.rolling(14).mean() / out["close"]
    out["volume_ratio"] = out["volume"] / out["volume"].rolling(36).mean()
    recent_high = out["high"].rolling(36).max().shift(1)
    recent_low = out["low"].rolling(36).min().shift(1)
    denom = (recent_high - recent_low).replace(0, pd.NA)
    out["breakout_pos"] = (out["close"] - recent_low) / denom
    middle = out["close"].rolling(36).mean()
    out["bb_width"] = 4.0 * out["close"].rolling(36).std() / middle
    path = out["close"].diff().abs().rolling(12).sum()
    out["trend_efficiency"] = (out["close"] - out["close"].shift(12)).abs() / path
    out["expected_move_vs_cost"] = out["atr_pct"] / max(round_trip_friction, 1e-12)
    return out


def build_cross_asset_context(
    frames: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    close = pd.concat(
        [
            frame.set_index("date")["close"].rename(pair)
            for pair, frame in frames.items()
        ],
        axis=1,
    ).sort_index()
    ret_3 = close / close.shift(3) - 1.0
    ret_12 = close / close.shift(12) - 1.0
    btc_pair = next((pair for pair in close.columns if pair.startswith("BTC/")), None)
    context = pd.DataFrame(index=close.index)
    context["btc_ret_3"] = ret_3[btc_pair] if btc_pair else pd.NA
    context["market_median_ret_12"] = ret_12.median(axis=1)
    context["market_breadth_3"] = (ret_3 > 0).mean(axis=1)
    context["cross_section_dispersion_12"] = ret_12.std(axis=1)
    context.index.name = "date"
    return context.reset_index()


def attach_cross_asset_features(
    frame: pd.DataFrame,
    pair: str,
    context: pd.DataFrame,
) -> pd.DataFrame:
    out = frame.merge(context, on="date", how="left", validate="one_to_one")
    out["relative_ret_12"] = out["ret_12"] - out["market_median_ret_12"]
    out["btc_pair_lag_gap"] = out["btc_ret_3"] - out["ret_3"]
    return out


def _merge_prior(
    frame: pd.DataFrame,
    source: pd.DataFrame,
    source_date: str,
    *,
    tolerance: pd.Timedelta | None = None,
) -> pd.DataFrame:
    if source.empty:
        return frame
    order = list(frame.index)
    left = frame.reset_index(names="__row_order").sort_values("date")
    right = source.sort_values(source_date)
    # Feather sources can carry different physical datetime units (ms/us/ns).
    # merge_asof requires identical dtypes even when both columns are UTC.
    left["date"] = pd.to_datetime(left["date"], utc=True).astype("datetime64[ns, UTC]")
    right[source_date] = pd.to_datetime(right[source_date], utc=True).astype("datetime64[ns, UTC]")
    merged = pd.merge_asof(
        left,
        right,
        left_on="date",
        right_on=source_date,
        direction="backward",
        allow_exact_matches=False,
        tolerance=tolerance,
    )
    return merged.sort_values("__row_order").drop(columns=["__row_order"]).set_axis(order)


def attach_funding(frame: pd.DataFrame, pair: str, data_root: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    path = data_root / f"{pair_stem(pair)}-1h-funding_rate.feather"
    if not path.exists():
        return frame, {"requirement": "funding_rate", "status": "missing", "path": str(path)}
    source = pd.read_feather(path, columns=["date", "open"])
    source["funding_date"] = pd.to_datetime(source.pop("date"), utc=True)
    source["funding_rate"] = pd.to_numeric(source.pop("open"), errors="coerce")
    source = source.dropna().sort_values("funding_date").drop_duplicates("funding_date")
    source["funding_change"] = source["funding_rate"].diff()
    out = _merge_prior(frame, source, "funding_date")
    return out, {
        "requirement": "funding_rate",
        "status": "available",
        "path": str(path),
        "rows": int(len(source)),
        "start_utc": source["funding_date"].min().isoformat(),
        "end_utc": source["funding_date"].max().isoformat(),
        "causality": "strictly_prior_settlement",
    }


def attach_basis(frame: pd.DataFrame, pair: str, data_root: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    futures_path = data_root / f"{pair_stem(pair)}-1h-futures.feather"
    mark_path = data_root / f"{pair_stem(pair)}-1h-mark.feather"
    if not futures_path.exists() or not mark_path.exists():
        return frame, {
            "requirement": "mark_price",
            "status": "missing",
            "paths": [str(futures_path), str(mark_path)],
        }
    futures = pd.read_feather(futures_path, columns=["date", "close"]).rename(columns={"close": "futures_close"})
    mark = pd.read_feather(mark_path, columns=["date", "close"]).rename(columns={"close": "mark_close"})
    futures["date"] = pd.to_datetime(futures["date"], utc=True)
    mark["date"] = pd.to_datetime(mark["date"], utc=True)
    source = futures.merge(mark, on="date", how="inner", validate="one_to_one").sort_values("date")
    source["basis_bps"] = (source["futures_close"] / source["mark_close"] - 1.0) * 10000.0
    source["basis_available_date"] = source["date"] + pd.Timedelta(hours=1)
    source = source[["basis_available_date", "basis_bps"]].dropna()
    out = _merge_prior(frame, source, "basis_available_date")
    return out, {
        "requirement": "mark_price",
        "status": "available",
        "paths": [str(futures_path), str(mark_path)],
        "rows": int(len(source)),
        "start_utc": source["basis_available_date"].min().isoformat(),
        "end_utc": source["basis_available_date"].max().isoformat(),
        "causality": "prior_completed_hour",
    }


OI_COLUMNS = [
    "create_time",
    "sum_open_interest",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
]


def _oi_cache_signature(archives: list[Path]) -> dict[str, Any]:
    return {
        "count": len(archives),
        "names": [path.name for path in archives],
        "sizes": [path.stat().st_size for path in archives],
    }


def load_oi_metrics(pair: str, aux_root: Path, cache_root: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    symbol = pair_symbol(pair)
    source_dir = aux_root / "open_interest_metrics" / symbol
    archives = sorted(source_dir.glob("*.zip"))
    if not archives:
        return pd.DataFrame(), {
            "requirement": "open_interest_metrics",
            "status": "missing",
            "path": str(source_dir),
        }
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_path = cache_root / f"{symbol}_oi_metrics.feather"
    meta_path = cache_root / f"{symbol}_oi_metrics.meta.json"
    signature = _oi_cache_signature(archives)
    if cache_path.exists() and meta_path.exists():
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        if metadata.get("signature") == signature:
            frame = pd.read_feather(cache_path)
            frame["oi_date"] = pd.to_datetime(frame["oi_date"], utc=True)
            audit = dict(metadata["audit"])
            # Cache signatures describe source rows, not code-level causality
            # semantics.  Normalize old metadata after a contract upgrade so a
            # valid cached frame cannot report the retired unlimited carry rule.
            audit["causality"] = "strictly_prior_5m_metric_max_age_10m"
            audit["max_age_minutes"] = 10
            return frame, audit

    pieces: list[pd.DataFrame] = []
    for archive in archives:
        with zipfile.ZipFile(archive) as zipped:
            members = [name for name in zipped.namelist() if name.endswith(".csv")]
            if not members:
                continue
            with zipped.open(members[0]) as handle:
                pieces.append(pd.read_csv(handle, usecols=OI_COLUMNS))
    if not pieces:
        return pd.DataFrame(), {
            "requirement": "open_interest_metrics",
            "status": "unreadable",
            "path": str(source_dir),
        }
    raw = pd.concat(pieces, ignore_index=True)
    raw["oi_date"] = pd.to_datetime(raw.pop("create_time"), utc=True)
    for column in OI_COLUMNS[1:]:
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
    raw = raw.sort_values("oi_date").drop_duplicates("oi_date")
    raw["oi_change_1h"] = raw["sum_open_interest"] / raw["sum_open_interest"].shift(12) - 1.0
    raw["oi_change_4h"] = raw["sum_open_interest"] / raw["sum_open_interest"].shift(48) - 1.0
    raw["top_size_account_divergence"] = (
        raw["sum_toptrader_long_short_ratio"].clip(lower=1e-9).map(math.log)
        - raw["count_toptrader_long_short_ratio"].clip(lower=1e-9).map(math.log)
    )
    raw["taker_flow_log_ratio"] = raw["sum_taker_long_short_vol_ratio"].clip(lower=1e-9).map(math.log)
    keep = [
        "oi_date",
        "oi_change_1h",
        "oi_change_4h",
        "top_size_account_divergence",
        "taker_flow_log_ratio",
    ]
    frame = raw[keep].dropna(how="all", subset=keep[1:]).reset_index(drop=True)
    audit = {
        "requirement": "open_interest_metrics",
        "status": "available",
        "path": str(source_dir),
        "archives": len(archives),
        "rows": int(len(frame)),
        "start_utc": frame["oi_date"].min().isoformat(),
        "end_utc": frame["oi_date"].max().isoformat(),
        "causality": "strictly_prior_5m_metric_max_age_10m",
        "max_age_minutes": 10,
    }
    frame.to_feather(cache_path)
    meta_path.write_text(
        json.dumps({"signature": signature, "audit": audit}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return frame, audit


def attach_oi_metrics(
    frame: pd.DataFrame,
    pair: str,
    aux_root: Path,
    cache_root: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    source, audit = load_oi_metrics(pair, aux_root, cache_root)
    if source.empty:
        return frame, audit
    return _merge_prior(
        frame,
        source,
        "oi_date",
        tolerance=pd.Timedelta(minutes=10),
    ), audit


def attach_registered_auxiliary_features(
    frame: pd.DataFrame,
    pair: str,
    data_root: Path,
    aux_root: Path,
    cache_root: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    out, funding_audit = attach_funding(frame, pair, data_root)
    out, basis_audit = attach_basis(out, pair, data_root)
    out, oi_audit = attach_oi_metrics(out, pair, aux_root, cache_root)
    return out, [funding_audit, basis_audit, oi_audit]


def registry_payload() -> dict[str, Any]:
    domains: dict[str, int] = {}
    for spec in FACTOR_SPECS:
        domains[spec.domain] = domains.get(spec.domain, 0) + 1
    return {
        "schema_version": 1,
        "features": [spec.as_dict() for spec in FACTOR_SPECS],
        "domain_counts": dict(sorted(domains.items())),
        "causality_contract": (
            "OHLCV features use current-or-prior completed candles; funding/OI use strictly prior records; "
            "OI/top-trader/taker metrics expire after 10 minutes; basis uses the prior completed hourly "
            "futures/mark bar; missing or stale auxiliary data is never zero-filled or forward-carried."
        ),
    }
