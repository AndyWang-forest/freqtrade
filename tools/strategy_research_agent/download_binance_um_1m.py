#!/usr/bin/env python3
"""Download Binance USDT-M OHLCV archives into Freqtrade feather files.

The historical command name is kept for compatibility with existing local
automation.  The downloader now supports the Agent's 1m/3m/5m/15m/1h data
contract and can append or prepend without contacting Binance trading APIs.
"""

from __future__ import annotations

import argparse
import io
import json
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd


BASE_URL = "https://data.binance.vision/data/futures/um"
SUPPORTED_TIMEFRAMES = {"1m", "3m", "5m", "15m", "1h"}
COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trades",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
]
REPORT_DIR = Path("user_data/strategy_research/data_updates")


def parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def default_end_day() -> date:
    return datetime.now(timezone.utc).date()


def month_start(day: date) -> date:
    return day.replace(day=1)


def add_month(day: date) -> date:
    year = day.year + (day.month // 12)
    month = (day.month % 12) + 1
    return date(year, month, 1)


def month_end_exclusive(day: date) -> date:
    return add_month(month_start(day))


def timeframe_delta(timeframe: str) -> pd.Timedelta:
    if timeframe.endswith("m"):
        return pd.Timedelta(minutes=int(timeframe[:-1]))
    if timeframe.endswith("h"):
        return pd.Timedelta(hours=int(timeframe[:-1]))
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def iter_urls(symbol: str, timeframe: str, start: date, end: date):
    """Yield monthly archives for whole months and daily archives otherwise."""
    current = start
    while current < end:
        if current.day == 1 and month_end_exclusive(current) <= end:
            yield (
                "monthly",
                current,
                f"{BASE_URL}/monthly/klines/{symbol}/{timeframe}/"
                f"{symbol}-{timeframe}-{current:%Y-%m}.zip",
            )
            current = add_month(current)
            continue

        yield (
            "daily",
            current,
            f"{BASE_URL}/daily/klines/{symbol}/{timeframe}/"
            f"{symbol}-{timeframe}-{current:%Y-%m-%d}.zip",
        )
        current += timedelta(days=1)


def fetch_bytes(url: str, cache_file: Path) -> bytes | None:
    if cache_file.exists() and cache_file.stat().st_size > 0:
        return cache_file.read_bytes()

    request = Request(url, headers={"User-Agent": "freqtrade-local-research/1.0"})
    payload: bytes | None = None
    for attempt in range(1, 4):
        try:
            with urlopen(request, timeout=60) as response:
                payload = response.read()
            break
        except HTTPError as exc:
            if exc.code == 404:
                print(f"skip missing: {url}")
                return None
            if attempt == 3:
                raise
            print(f"retry {attempt}/3 after HTTP {exc.code}: {url}")
        except (TimeoutError, URLError, IncompleteRead, ConnectionError) as exc:
            if attempt == 3:
                raise RuntimeError(f"failed to download {url}: {exc}") from exc
            print(f"retry {attempt}/3 after network error: {url} ({exc})")
        time.sleep(2**attempt)

    if payload is None:
        raise RuntimeError(f"failed to download {url}: empty response")

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_bytes(payload)
    return payload


def frame_from_zip(payload: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        csv_names = [name for name in archive.namelist() if name.endswith(".csv")]
        if len(csv_names) != 1:
            raise RuntimeError(f"expected one csv in archive, found {csv_names}")
        with archive.open(csv_names[0]) as handle:
            frame = pd.read_csv(handle, header=None, names=COLUMNS)

    frame["open_time"] = pd.to_numeric(frame["open_time"], errors="coerce")
    frame = frame.dropna(subset=["open_time"])
    frame["date"] = pd.to_datetime(frame["open_time"].astype("int64"), unit="ms", utc=True)
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame[["date", "open", "high", "low", "close", "volume"]].dropna()


def output_name(symbol: str, timeframe: str) -> str:
    base = symbol.removesuffix("USDT")
    return f"{base}_USDT_USDT-{timeframe}-futures.feather"


def existing_data(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    frame = pd.read_feather(path)
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    return frame[["date", "open", "high", "low", "close", "volume"]]


def incremental_start(existing: pd.DataFrame, fallback_start: date, timeframe: str) -> date:
    if existing.empty:
        return fallback_start
    last_ts = existing["date"].max()
    return (last_ts + timeframe_delta(timeframe)).date()


def count_gaps(data: pd.DataFrame, timeframe: str) -> int:
    gaps = int(data["date"].diff().ne(timeframe_delta(timeframe)).sum() - 1)
    return max(gaps, 0)


def missing_days(
    data: pd.DataFrame,
    timeframe: str,
    start: date,
    end: date,
) -> list[date]:
    if data.empty:
        return []
    delta = timeframe_delta(timeframe)
    ordered = data["date"].sort_values().reset_index(drop=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    days: set[date] = set()
    for previous, current in zip(ordered.iloc[:-1], ordered.iloc[1:], strict=True):
        if current - previous <= delta:
            continue
        cursor = previous + delta
        while cursor < current:
            if start_ts <= cursor < end_ts:
                days.add(cursor.date())
            cursor += delta
    return sorted(days)


def unchanged_result(
    symbol: str,
    timeframe: str,
    prior: pd.DataFrame,
    out_file: Path,
    status: str,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "status": status,
        "rows": int(len(prior)),
        "first_utc": prior["date"].min().isoformat() if not prior.empty else None,
        "last_utc": prior["date"].max().isoformat() if not prior.empty else None,
        "gaps": count_gaps(prior, timeframe) if not prior.empty else None,
        "archives": 0,
        "output": str(out_file),
    }


def download_symbol(
    symbol: str,
    timeframe: str,
    start: date,
    end: date,
    data_dir: Path,
    cache_dir: Path,
    incremental: bool,
    prepend: bool,
    repair_gaps: bool,
) -> dict[str, object]:
    out_file = data_dir / "futures" / output_name(symbol, timeframe)
    merge_existing = incremental or prepend or repair_gaps
    prior = existing_data(out_file) if merge_existing else pd.DataFrame()
    effective_start = start
    effective_end = end

    if repair_gaps:
        if prior.empty:
            raise RuntimeError(f"cannot repair gaps without existing data: {out_file}")
        repair_days = missing_days(prior, timeframe, start, end)
        if not repair_days:
            print(f"{symbol} {timeframe}: no internal gaps in requested range")
            return unchanged_result(symbol, timeframe, prior, out_file, "up_to_date")
        frames: list[pd.DataFrame] = []
        downloaded = 0
        for repair_day in repair_days:
            url = (
                f"{BASE_URL}/daily/klines/{symbol}/{timeframe}/"
                f"{symbol}-{timeframe}-{repair_day:%Y-%m-%d}.zip"
            )
            cache_file = (
                cache_dir
                / "daily"
                / symbol
                / timeframe
                / f"{symbol}-{timeframe}-{repair_day:%Y-%m-%d}.zip"
            )
            payload = fetch_bytes(url, cache_file)
            if payload is None:
                continue
            frames.append(frame_from_zip(payload))
            downloaded += 1
            print(f"{symbol} {timeframe}: repaired daily {repair_day:%Y-%m-%d}")
        if not frames:
            return unchanged_result(symbol, timeframe, prior, out_file, "gaps_unresolved")
        data = pd.concat([prior, *frames], ignore_index=True)
        data = data.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
        delta = timeframe_delta(timeframe)
        expected = int((data["date"].max() - data["date"].min()) / delta) + 1
        gaps = count_gaps(data, timeframe)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        data.to_feather(out_file, compression="lz4", compression_level=9)
        print(
            f"{symbol} {timeframe}: wrote repaired {out_file} | rows={len(data):,}/{expected:,} "
            f"| gaps={gaps} | daily_archives={downloaded}"
        )
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "repaired" if gaps == 0 else "gaps_remaining",
            "rows": int(len(data)),
            "expected_rows": expected,
            "first_utc": data["date"].min().isoformat(),
            "last_utc": data["date"].max().isoformat(),
            "gaps": gaps,
            "archives": downloaded,
            "repair_days": [item.isoformat() for item in repair_days],
            "output": str(out_file),
        }

    if incremental:
        effective_start = incremental_start(prior, start, timeframe)
    elif prepend and not prior.empty:
        effective_end = min(end, prior["date"].min().date())

    if effective_start >= effective_end:
        print(
            f"{symbol} {timeframe}: requested range already covered by "
            f"{prior['date'].min() if prepend and not prior.empty else prior['date'].max() if not prior.empty else 'none'}"
        )
        return unchanged_result(symbol, timeframe, prior, out_file, "up_to_date")

    frames: list[pd.DataFrame] = []
    downloaded = 0
    for archive_type, archive_date, url in iter_urls(
        symbol, timeframe, effective_start, effective_end
    ):
        suffix = archive_date.strftime("%Y-%m" if archive_type == "monthly" else "%Y-%m-%d")
        cache_file = (
            cache_dir
            / archive_type
            / symbol
            / timeframe
            / f"{symbol}-{timeframe}-{suffix}.zip"
        )
        payload = fetch_bytes(url, cache_file)
        if payload is None:
            continue
        frames.append(frame_from_zip(payload))
        downloaded += 1
        print(f"{symbol} {timeframe}: loaded {archive_type} {suffix}")

    if not frames:
        if merge_existing and not prior.empty:
            print(f"{symbol} {timeframe}: no new archives available")
            return unchanged_result(symbol, timeframe, prior, out_file, "no_new_archives")
        raise RuntimeError(f"no data downloaded for {symbol} {timeframe}")

    new_data = pd.concat(frames, ignore_index=True)
    start_ts = pd.Timestamp(effective_start, tz="UTC")
    end_ts = pd.Timestamp(effective_end, tz="UTC")
    new_data = new_data[(new_data["date"] >= start_ts) & (new_data["date"] < end_ts)]
    data = (
        pd.concat([prior, new_data], ignore_index=True)
        if merge_existing and not prior.empty
        else new_data
    )
    data = data.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)

    delta = timeframe_delta(timeframe)
    expected = int((data["date"].max() - data["date"].min()) / delta) + 1
    actual = len(data)
    gaps = count_gaps(data, timeframe)

    out_file.parent.mkdir(parents=True, exist_ok=True)
    data.to_feather(out_file, compression="lz4", compression_level=9)

    print(
        f"{symbol} {timeframe}: wrote {out_file} | rows={actual:,}/{expected:,} "
        f"| range={data['date'].min()} -> {data['date'].max()} | gaps={gaps} "
        f"| archives={downloaded}"
    )
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "status": "updated",
        "rows": actual,
        "expected_rows": expected,
        "first_utc": data["date"].min().isoformat(),
        "last_utc": data["date"].max().isoformat(),
        "gaps": gaps,
        "archives": downloaded,
        "requested_start": effective_start.isoformat(),
        "requested_end_exclusive": effective_end.isoformat(),
        "output": str(out_file),
    }


def write_report(results: list[dict[str, object]], timeframes: list[str]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "source": "data.binance.vision_futures_um_public_archives",
        "results": results,
    }
    json_path = REPORT_DIR / "latest_ohlcv_update.json"
    md_path = REPORT_DIR / "latest_ohlcv_update.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# Binance USDT-M OHLCV Update",
        "",
        f"- Generated UTC: `{payload['generated_at_utc']}`",
        f"- Source: `{payload['source']}`",
        "",
        "| Symbol | Timeframe | Status | Rows | First | Last | Gaps | Archives |",
        "|---|---|---|---:|---|---|---:|---:|",
    ]
    for item in results:
        lines.append(
            "| {symbol} | {timeframe} | {status} | {rows} | {first_utc} | "
            "{last_utc} | {gaps} | {archives} |".format(**item)
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Preserve the existing dashboard pointer used by 1m incremental refreshes.
    if set(timeframes) == {"1m"}:
        (REPORT_DIR / "latest_ohlcv_1m_update.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (REPORT_DIR / "latest_ohlcv_1m_update.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--timeframes", nargs="+", default=["1m"])
    parser.add_argument("--start", type=parse_day, default=parse_day("2024-01-01"))
    parser.add_argument("--end", type=parse_day, default=default_end_day())
    parser.add_argument("--data-dir", type=Path, default=Path("user_data/data/binance"))
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("user_data/data/binance/public_data_cache")
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--incremental", action="store_true", help="Append newer archives.")
    mode.add_argument("--prepend", action="store_true", help="Merge older archives before existing data.")
    mode.add_argument(
        "--repair-gaps",
        action="store_true",
        help="Fill internal gaps from daily public archives.",
    )
    args = parser.parse_args()

    unsupported = sorted(set(args.timeframes) - SUPPORTED_TIMEFRAMES)
    if unsupported:
        raise SystemExit(f"unsupported timeframes: {unsupported}")
    if args.end <= args.start:
        raise SystemExit("--end must be after --start")

    results = []
    for symbol in args.symbols:
        for timeframe in args.timeframes:
            results.append(
                download_symbol(
                    symbol,
                    timeframe,
                    args.start,
                    args.end,
                    args.data_dir,
                    args.cache_dir,
                    args.incremental,
                    args.prepend,
                    args.repair_gaps,
                )
            )
    write_report(results, args.timeframes)


if __name__ == "__main__":
    main()
