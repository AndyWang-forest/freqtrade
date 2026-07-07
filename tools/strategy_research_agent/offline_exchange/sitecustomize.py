"""Offline CCXT market metadata for local Freqtrade research commands.

Enable explicitly with:
PYTHONPATH=user_data/offline_exchange freqtrade backtesting ...

This avoids live exchangeInfo requests when local network access to Binance
public metadata endpoints is unavailable. It provides BTC/ETH core markets
plus the explicit high-liquidity research extension futures pairs for
local-data backtesting / recursive-analysis.
"""

from __future__ import annotations


CORE_BASES = ("BTC", "ETH")
RESEARCH_EXTENSION_BASES = ("SOL", "BNB", "XRP")
ALL_RESEARCH_BASES = CORE_BASES + RESEARCH_EXTENSION_BASES


def _spot_market(base: str) -> dict:
    symbol = f"{base}/USDT"
    return {
        "id": f"{base}USDT",
        "lowercaseId": f"{base.lower()}usdt",
        "symbol": symbol,
        "base": base,
        "quote": "USDT",
        "settle": None,
        "baseId": base,
        "quoteId": "USDT",
        "settleId": None,
        "type": "spot",
        "spot": True,
        "margin": False,
        "swap": False,
        "future": False,
        "option": False,
        "active": True,
        "contract": False,
        "linear": None,
        "inverse": None,
        "taker": 0.001,
        "maker": 0.001,
        "contractSize": None,
        "expiry": None,
        "expiryDatetime": None,
        "strike": None,
        "optionType": None,
        "precision": {"amount": 0.000001, "price": 0.01, "cost": 0.01},
        "limits": {
            "amount": {"min": 0.000001, "max": None},
            "price": {"min": 0.01, "max": None},
            "cost": {"min": 5.0, "max": None},
            "leverage": {"min": 1.0, "max": 1.0},
        },
        "info": {"symbol": f"{base}USDT", "status": "TRADING"},
    }


def _linear_swap_market(base: str) -> dict:
    symbol = f"{base}/USDT:USDT"
    return {
        "id": f"{base}USDT",
        "lowercaseId": f"{base.lower()}usdt",
        "symbol": symbol,
        "base": base,
        "quote": "USDT",
        "settle": "USDT",
        "baseId": base,
        "quoteId": "USDT",
        "settleId": "USDT",
        "type": "swap",
        "spot": False,
        "margin": False,
        "swap": True,
        "future": False,
        "option": False,
        "active": True,
        "contract": True,
        "linear": True,
        "inverse": False,
        "taker": 0.0005,
        "maker": 0.0002,
        "contractSize": 1.0,
        "expiry": None,
        "expiryDatetime": None,
        "strike": None,
        "optionType": None,
        "precision": {"amount": 0.001, "price": 0.01, "cost": 0.01},
        "limits": {
            "amount": {"min": 0.001, "max": None},
            "price": {"min": 0.01, "max": None},
            "cost": {"min": 5.0, "max": None},
            "leverage": {"min": 1.0, "max": 125.0},
        },
        "info": {
            "symbol": f"{base}USDT",
            "status": "TRADING",
            "contractType": "PERPETUAL",
            "marginAsset": "USDT",
        },
    }


def _offline_markets(options: dict | None = None) -> list[dict]:
    options = options or {}
    fetch_markets = options.get("fetchMarkets")
    if isinstance(fetch_markets, dict):
        requested = fetch_markets.get("types", [])
    else:
        requested = fetch_markets or []

    default_type = options.get("defaultType")
    include_spot = (not requested and default_type in (None, "spot")) or "spot" in requested
    include_linear = (
        default_type in ("future", "swap", "linear")
        or "linear" in requested
        or "future" in requested
        or "swap" in requested
    )

    markets: list[dict] = []
    if include_spot:
        markets.extend(_spot_market(base) for base in CORE_BASES)
    if include_linear:
        markets.extend(_linear_swap_market(base) for base in ALL_RESEARCH_BASES)
    if not markets:
        markets.extend(_spot_market(base) for base in CORE_BASES)
    return markets


def _patch_ccxt_binance() -> None:
    try:
        from ccxt import binance as sync_binance
    except Exception:
        sync_binance = None

    if sync_binance is not None:

        def fetch_markets(self, params=None):
            return _offline_markets(getattr(self, "options", {}))

        sync_binance.fetch_markets = fetch_markets

    try:
        from ccxt.async_support import binance as async_binance
    except Exception:
        async_binance = None

    if async_binance is not None:

        async def fetch_markets(self, params=None):
            return _offline_markets(getattr(self, "options", {}))

        async_binance.fetch_markets = fetch_markets


_patch_ccxt_binance()
