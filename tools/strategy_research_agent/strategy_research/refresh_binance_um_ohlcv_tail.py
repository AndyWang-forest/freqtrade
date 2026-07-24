#!/usr/bin/env python3
"""Refresh closed Binance USDT-M OHLCV candles without reading trade outcomes."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
from pair_universe import pairs_for_scope
from repo_paths import find_repo_root


REPO_ROOT = find_repo_root()
DATA_DIR = REPO_ROOT / "user_data/data/binance/futures"
REPORT_DIR = REPO_ROOT / "user_data/strategy_research/data_updates"
DEFAULT_BASE_URLS = (
    "https://fapi.binance.com",
    "https://fapi1.binance.com",
    "https://fapi2.binance.com",
    "https://fapi3.binance.com",
    "https://fapi4.binance.com",
)
TIMEFRAME_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "1h": 60}
OHLCV_COLUMNS = ("date", "open", "high", "low", "close", "volume")


def utc_tag() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def pair_symbol(pair: str) -> str:
    base, remainder = pair.split("/", 1)
    quote = remainder.split(":", 1)[0]
    return f"{base}{quote}"


def pair_data_path(pair: str, timeframe: str) -> Path:
    stem = pair.replace("/", "_").replace(":", "_")
    return DATA_DIR / f"{stem}-{timeframe}-futures.feather"


def timeframe_delta(timeframe: str) -> pd.Timedelta:
    return pd.Timedelta(minutes=TIMEFRAME_MINUTES[timeframe])


def last_closed_open(now: pd.Timestamp, timeframe: str) -> pd.Timestamp:
    delta = timeframe_delta(timeframe)
    return now.floor(delta) - delta


def fetch_klines(
    symbol: str,
    timeframe: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    base_urls: tuple[str, ...],
    timeout_seconds: float,
) -> tuple[pd.DataFrame, str]:
    interval_ms = int(timeframe_delta(timeframe).total_seconds() * 1000)
    cursor_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    rows: list[list[Any]] = []
    successful_base = ""

    while cursor_ms <= end_ms:
        params = urlencode(
            {
                "symbol": symbol,
                "interval": timeframe,
                "startTime": cursor_ms,
                "endTime": end_ms,
                "limit": 1500,
            }
        )
        errors: list[str] = []
        page: list[list[Any]] | None = None
        for base_url in base_urls:
            if not base_url.startswith("https://"):
                errors.append(f"{base_url}: only HTTPS endpoints are allowed")
                continue
            request = Request(  # noqa: S310 - HTTPS is enforced immediately above.
                f"{base_url}/fapi/v1/klines?{params}",
                headers={"User-Agent": "freqtrade-strategy-research/1.0"},
            )
            try:
                with urlopen(  # noqa: S310 - request URL is restricted to HTTPS.
                    request, timeout=timeout_seconds
                ) as response:
                    decoded = json.loads(response.read().decode("utf-8"))
                if not isinstance(decoded, list):
                    raise ValueError(f"unexpected response type: {type(decoded).__name__}")
                page = decoded
                successful_base = base_url
                break
            except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"{base_url}: {type(exc).__name__}: {exc}")
        if page is None:
            raise RuntimeError("all Binance futures REST endpoints failed: " + " | ".join(errors))
        if not page:
            break
        rows.extend(page)
        last_open_ms = int(page[-1][0])
        next_cursor = last_open_ms + interval_ms
        if next_cursor <= cursor_ms:
            raise RuntimeError("Binance kline pagination did not advance")
        cursor_ms = next_cursor
        if len(page) < 1500:
            break
        time.sleep(0.05)

    if not rows:
        return pd.DataFrame(
            columns=["date", "open", "high", "low", "close", "volume"]
        ), successful_base
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime([int(row[0]) for row in rows], unit="ms", utc=True),
            "open": [float(row[1]) for row in rows],
            "high": [float(row[2]) for row in rows],
            "low": [float(row[3]) for row in rows],
            "close": [float(row[4]) for row in rows],
            "volume": [float(row[5]) for row in rows],
        }
    )
    frame = frame.loc[frame["date"] <= end].drop_duplicates("date").sort_values("date")
    return frame, successful_base


def gap_count(dates: pd.Series, timeframe: str) -> int:
    if len(dates) < 2:
        return 0
    ordered = pd.to_datetime(dates, utc=True).sort_values().drop_duplicates()
    delta = timeframe_delta(timeframe)
    expected = int((ordered.iloc[-1] - ordered.iloc[0]) / delta) + 1
    return max(expected - len(ordered), 0)


def atomic_write_feather(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    try:
        frame.to_feather(temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def refresh_pair(
    pair: str,
    timeframe: str,
    now: pd.Timestamp,
    base_urls: tuple[str, ...],
    timeout_seconds: float,
) -> dict[str, Any]:
    path = pair_data_path(pair, timeframe)
    if not path.exists():
        raise FileNotFoundError(f"missing local seed data: {relative(path)}")
    existing = pd.read_feather(path)
    missing = sorted(set(OHLCV_COLUMNS) - set(existing.columns))
    if missing:
        raise ValueError(f"{relative(path)} missing columns: {', '.join(missing)}")
    existing = existing[list(OHLCV_COLUMNS)].copy()
    existing["date"] = pd.to_datetime(existing["date"], utc=True)
    existing = existing.sort_values("date").drop_duplicates("date")
    before_rows = len(existing)
    before_last = existing["date"].iloc[-1]
    closed_end = last_closed_open(now, timeframe)
    closed_existing = existing.loc[existing["date"] <= closed_end].copy()
    unclosed_rows_removed = before_rows - len(closed_existing)
    if closed_existing.empty:
        raise ValueError(f"{relative(path)} has no candle closed by {closed_end.isoformat()}")

    # Re-fetch the last locally closed candle. It may have been persisted while
    # still in progress by another downloader, so appending from +1 interval is
    # not sufficient to guarantee closed-candle-only inputs.
    fetch_start = closed_existing["date"].iloc[-1]

    fetched, endpoint = fetch_klines(
        pair_symbol(pair),
        timeframe,
        fetch_start,
        closed_end,
        base_urls,
        timeout_seconds,
    )
    if fetched.empty:
        raise RuntimeError(
            f"Binance returned no authoritative closed candles for {pair} "
            f"from {fetch_start.isoformat()} through {closed_end.isoformat()}"
        )
    fetched_dates = set(pd.to_datetime(fetched["date"], utc=True))
    missing_boundaries = [
        boundary.isoformat()
        for boundary in (fetch_start, closed_end)
        if boundary not in fetched_dates
    ]
    if missing_boundaries:
        raise RuntimeError(
            f"Binance response for {pair} omitted required closed candle open(s): "
            + ", ".join(missing_boundaries)
        )
    merged = pd.concat([closed_existing, fetched], ignore_index=True)
    merged["date"] = pd.to_datetime(merged["date"], utc=True)
    merged = merged.loc[merged["date"] <= closed_end]
    merged = merged.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    merged = merged[list(OHLCV_COLUMNS)]
    missing_intervals = gap_count(merged["date"], timeframe)
    if missing_intervals:
        raise ValueError(
            f"{relative(path)} contains {missing_intervals} missing {timeframe} intervals"
        )
    if merged["date"].iloc[-1] != closed_end:
        raise RuntimeError(
            f"{relative(path)} remains stale at {merged['date'].iloc[-1].isoformat()}; "
            f"expected closed open {closed_end.isoformat()}"
        )
    if unclosed_rows_removed or not fetched.empty or len(merged) != before_rows:
        atomic_write_feather(path, merged)

    return {
        "pair": pair,
        "symbol": pair_symbol(pair),
        "timeframe": timeframe,
        "path": relative(path),
        "rows_before": before_rows,
        "rows_after": len(merged),
        "rows_added": len(merged) - before_rows,
        "unclosed_rows_removed": unclosed_rows_removed,
        "first_open_utc": merged["date"].iloc[0].isoformat(),
        "last_open_before_utc": before_last.isoformat(),
        "last_closed_open_target_utc": closed_end.isoformat(),
        "last_open_after_utc": merged["date"].iloc[-1].isoformat(),
        "missing_intervals": missing_intervals,
        "endpoint": endpoint,
    }


def write_report(payload: dict[str, Any]) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    tag = payload["generated_at_utc"]
    json_path = REPORT_DIR / f"ohlcv_rest_tail_{tag}.json"
    md_path = REPORT_DIR / f"ohlcv_rest_tail_{tag}.md"
    json_text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    json_path.write_text(json_text, encoding="utf-8")
    (REPORT_DIR / "latest_ohlcv_rest_tail.json").write_text(json_text, encoding="utf-8")
    lines = [
        "# Binance USDT-M OHLCV Tail Refresh",
        "",
        f"- Generated UTC: `{tag}`",
        f"- Pair scope: `{payload['pair_scope']}`",
        f"- Timeframe: `{payload['timeframe']}`",
        "- Closed candles only: `True`",
        "- Outcomes read: `False`",
        "",
        "| Pair | Added | Trimmed Unclosed | Rows | Before Last | After Last | "
        "Missing Intervals | Endpoint |",
        "|---|---:|---:|---:|---|---|---:|---|",
    ]
    for row in payload["rows"]:
        lines.append(
            f"| `{row['pair']}` | {row['rows_added']} | "
            f"{row['unclosed_rows_removed']} | {row['rows_after']} | "
            f"`{row['last_open_before_utc']}` | `{row['last_open_after_utc']}` | "
            f"{row['missing_intervals']} | `{row['endpoint']}` |"
        )
    markdown = "\n".join(lines) + "\n"
    md_path.write_text(markdown, encoding="utf-8")
    (REPORT_DIR / "latest_ohlcv_rest_tail.md").write_text(markdown, encoding="utf-8")
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pair-scope",
        choices=["core", "extension", "research_all"],
        default="research_all",
    )
    parser.add_argument("--timeframe", choices=sorted(TIMEFRAME_MINUTES), default="3m")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument(
        "--base-url",
        action="append",
        dest="base_urls",
        help="Override Binance futures REST endpoint; repeat for fallbacks.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    now = pd.Timestamp.now(tz="UTC")
    base_urls = tuple(args.base_urls or DEFAULT_BASE_URLS)
    rows = [
        refresh_pair(
            pair,
            args.timeframe,
            now,
            base_urls,
            args.timeout_seconds,
        )
        for pair in pairs_for_scope(args.pair_scope)
    ]
    payload = {
        "generated_at_utc": utc_tag(),
        "status": "closed_candle_tail_refreshed",
        "research_only": True,
        "outcomes_read": False,
        "pair_scope": args.pair_scope,
        "timeframe": args.timeframe,
        "closed_candles_only": True,
        "rows": rows,
    }
    json_path, md_path = write_report(payload)
    print(relative(json_path))
    print(relative(md_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
