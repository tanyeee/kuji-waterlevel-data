#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.request import urlopen
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
INVALID_FLAGS = {"-", "$", "#"}


def read_json(location: str) -> dict[str, Any]:
    if location.startswith(("http://", "https://")):
        with urlopen(location, timeout=60) as response:
            return json.load(response)
    return json.loads(Path(location).read_text(encoding="utf-8"))


def source_location(base: str, station_id: str, filename: str) -> str:
    if base.startswith(("http://", "https://")):
        return f"{base.rstrip('/')}/{station_id}/{filename}"
    return str(Path(base) / station_id / filename)


def write_json_if_changed(path: Path, payload: dict[str, Any]) -> bool:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == encoded:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")
    return True


def empty_values(day: date, step_minutes: int) -> list[float | None]:
    return [None] * (24 * 60 // step_minutes)


def archive_recent(config: dict[str, Any], source_base: str, output: Path, delay_days: int, today: date) -> int:
    station_payloads: dict[str, dict[str, Any]] = {}
    candidate_dates: set[date] = set()
    cutoff = today - timedelta(days=delay_days)
    for station in config["stations"]:
        payload = read_json(source_location(source_base, station["id"], "recent_10min.json"))
        station_payloads[station["id"]] = payload
        for record in payload.get("records", []):
            try:
                observed = datetime.fromisoformat(record["timestamp"]).date()
            except (KeyError, TypeError, ValueError):
                continue
            if observed <= cutoff:
                candidate_dates.add(observed)

    changed = 0
    for day in sorted(candidate_dates):
        destination = output / "10min" / f"{day:%Y}" / f"{day:%m}" / f"{day:%Y-%m-%d}.json"
        if destination.exists():
            continue
        archived_stations: dict[str, Any] = {}
        for station in config["stations"]:
            values = empty_values(day, 10)
            flags: dict[str, str] = {}
            for record in station_payloads[station["id"]].get("records", []):
                try:
                    observed = datetime.fromisoformat(record["timestamp"])
                except (KeyError, TypeError, ValueError):
                    continue
                if observed.date() != day:
                    continue
                index = (observed.hour * 60 + observed.minute) // 10
                flag = str(record.get("flag") or "")
                value = record.get("value")
                values[index] = value if isinstance(value, (int, float)) and flag not in INVALID_FLAGS else None
                if flag:
                    flags[str(index)] = flag
            archived_stations[station["id"]] = {"values": values}
            if flags:
                archived_stations[station["id"]]["flags"] = flags
        payload = {
            "schemaVersion": 1,
            "date": day.isoformat(),
            "stepMinutes": 10,
            "stations": archived_stations,
            "source": "river.go.jp via tanyeee/kuji-waterlevel",
        }
        changed += int(write_json_if_changed(destination, payload))
    return changed


def year_minutes(year: int, step_minutes: int) -> int:
    return ((date(year + 1, 1, 1) - date(year, 1, 1)).days * 24 * 60) // step_minutes


def archive_hourly(config: dict[str, Any], source_base: str, output: Path) -> int:
    changed = 0
    for station in config["stations"]:
        merged: dict[str, dict[str, Any]] = {}
        for filename in ("historical_hourly.json", "recent_hourly.json"):
            payload = read_json(source_location(source_base, station["id"], filename))
            for record in payload.get("records", []):
                timestamp = record.get("timestamp")
                if isinstance(timestamp, str):
                    merged[timestamp] = record
        years = sorted({int(timestamp[:4]) for timestamp in merged if len(timestamp) >= 4})
        for year in years:
            values: list[float | None] = [None] * year_minutes(year, 60)
            flags: dict[str, str] = {}
            start = datetime(year, 1, 1)
            for timestamp, record in merged.items():
                if not timestamp.startswith(f"{year:04d}-"):
                    continue
                try:
                    observed = datetime.fromisoformat(timestamp)
                except ValueError:
                    continue
                index = int((observed - start).total_seconds() // 3600)
                if not 0 <= index < len(values):
                    continue
                flag = str(record.get("flag") or "")
                value = record.get("value")
                values[index] = value if isinstance(value, (int, float)) and flag not in INVALID_FLAGS else None
                if flag:
                    flags[str(index)] = flag
            payload = {
                "schemaVersion": 1,
                "year": year,
                "stepMinutes": 60,
                "station": station["id"],
                "values": values,
                "source": "river.go.jp via tanyeee/kuji-waterlevel",
            }
            if flags:
                payload["flags"] = flags
            destination = output / "hourly" / station["id"] / f"{year}.json"
            changed += int(write_json_if_changed(destination, payload))
    return changed


def update_manifest(config: dict[str, Any], output: Path) -> None:
    daily_files = sorted((output / "10min").glob("*/*/*.json"))
    hourly_files = sorted((output / "hourly").glob("*/*.json"))
    dates = [path.stem for path in daily_files]
    hourly_years: dict[str, list[int]] = {}
    for path in hourly_files:
        hourly_years.setdefault(path.parent.name, []).append(int(path.stem))
    payload = {
        "schemaVersion": 1,
        "stations": config["stations"],
        "tenMinute": {"firstDate": dates[0] if dates else None, "lastDate": dates[-1] if dates else None},
        "hourlyYears": hourly_years,
    }
    write_json_if_changed(output / "manifest.json", payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--source-base")
    parser.add_argument("--output", default="data")
    parser.add_argument("--delay-days", type=int, default=2)
    parser.add_argument("--today", help="JST date override for tests (YYYY-MM-DD)")
    parser.add_argument("--skip-recent", action="store_true")
    parser.add_argument("--skip-hourly", action="store_true")
    args = parser.parse_args()
    config = read_json(args.config)
    source_base = args.source_base or config["sourceBaseUrl"]
    output = Path(args.output)
    today = date.fromisoformat(args.today) if args.today else datetime.now(JST).date()
    recent_changes = 0 if args.skip_recent else archive_recent(config, source_base, output, args.delay_days, today)
    hourly_changes = 0 if args.skip_hourly else archive_hourly(config, source_base, output)
    update_manifest(config, output)
    print(f"archived daily files: {recent_changes}; updated hourly files: {hourly_changes}")


if __name__ == "__main__":
    main()
