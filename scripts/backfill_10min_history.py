#!/usr/bin/env python3
"""Recover missing daily 10-minute archives from the old repository's snapshots.

The source's recent_10min.json files roll over after seven days. Commits four
days apart overlap, allowing us to verify coverage without rewriting existing
immutable archives.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as daytime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

try:
    from scripts.archive_water_levels import INVALID_FLAGS, JST, write_json_if_changed
except ModuleNotFoundError:
    from archive_water_levels import INVALID_FLAGS, JST, write_json_if_changed

REPOSITORY = "tanyeee/kuji-waterlevel"
STATION_FILE = "data/stations/{station}/recent_10min.json"
SNAPSHOT_INTERVAL_DAYS = 4


def github_token() -> str:
    result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def get_json(url: str, token: str = "") -> dict[str, Any] | list[Any]:
    headers = {"User-Agent": "kuji-waterlevel-data-backfill", "Accept": "application/vnd.github+json"}
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers=headers), timeout=60) as response:
                return json.load(response)
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def snapshot_days(first: date, last: date) -> list[date]:
    day = first + timedelta(days=3)
    limit = last + timedelta(days=3)
    result = []
    while day <= limit:
        result.append(day)
        day += timedelta(days=SNAPSHOT_INTERVAL_DAYS)
    return result


def commit_before(day: date, token: str) -> tuple[str, str]:
    local_time = datetime.combine(day, daytime(12), JST)
    until = local_time.astimezone(timezone.utc).isoformat(timespec="seconds")
    query = urlencode({"until": until, "per_page": 1})
    payload = get_json(f"https://api.github.com/repos/{REPOSITORY}/commits?{query}", token)
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"No commit exists before {day}")
    item = payload[0]
    return item["sha"], item["commit"]["committer"]["date"]


def station_snapshot(sha: str, station: str) -> dict[str, Any]:
    filename = STATION_FILE.format(station=station)
    url = f"https://raw.githubusercontent.com/{REPOSITORY}/{sha}/{quote(filename)}"
    payload = get_json(url)
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError(f"Invalid source file for {station} at {sha}")
    return payload


def archive_day(day: date, station_ids: list[str], records: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    stations = {}
    for station_id in station_ids:
        values: list[float | None] = [None] * 144
        flags: dict[str, str] = {}
        for record in records[station_id].values():
            observed = datetime.fromisoformat(record["timestamp"])
            if observed.date() != day:
                continue
            index = observed.hour * 6 + observed.minute // 10
            flag = str(record.get("flag") or "")
            value = record.get("value")
            if isinstance(value, (int, float)) and not isinstance(value, bool) and flag not in INVALID_FLAGS:
                values[index] = value
            if flag:
                flags[str(index)] = flag
        stations[station_id] = {"values": values}
        if flags:
            stations[station_id]["flags"] = flags
    return {
        "schemaVersion": 1,
        "date": day.isoformat(),
        "stepMinutes": 10,
        "stations": stations,
        "source": "river.go.jp via tanyeee/kuji-waterlevel Git history",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first", type=date.fromisoformat, required=True)
    parser.add_argument("--last", type=date.fromisoformat, required=True)
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument("--report", type=Path, default=Path("reports/backfill-10min-2026.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.last < args.first:
        parser.error("--last precedes --first")

    config = json.loads(args.config.read_text(encoding="utf-8"))
    station_ids = [station["id"] for station in config["stations"]]
    token = github_token()
    snapshots = []
    for day in snapshot_days(args.first, args.last):
        sha, committed_at = commit_before(day, token)
        snapshots.append({"requestedDate": day.isoformat(), "commit": sha, "committedAt": committed_at})
        print(f"snapshot {day}: {sha[:12]} ({committed_at})", flush=True)

    merged: dict[str, dict[str, dict[str, Any]]] = {station: {} for station in station_ids}
    conflicts: dict[str, int] = defaultdict(int)
    with ThreadPoolExecutor(max_workers=6) as executor:
        jobs = {
            executor.submit(station_snapshot, snapshot["commit"], station): (index, station)
            for index, snapshot in enumerate(snapshots)
            for station in station_ids
        }
        fetched = []
        for job in as_completed(jobs):
            index, station = jobs[job]
            fetched.append((index, station, job.result()))

    for index, station, payload in sorted(fetched, key=lambda item: item[0]):
        in_range = 0
        for record in payload["records"]:
            timestamp = record.get("timestamp")
            if not isinstance(timestamp, str):
                continue
            try:
                observed = datetime.fromisoformat(timestamp)
            except ValueError:
                continue
            if not args.first <= observed.date() <= args.last or observed.minute % 10 or observed.second:
                continue
            earlier = merged[station].get(timestamp)
            if earlier is not None and (earlier.get("value"), earlier.get("flag")) != (record.get("value"), record.get("flag")):
                conflicts[station] += 1
            merged[station][timestamp] = record
            in_range += 1
        snapshots[index].setdefault("recordCounts", {})[station] = in_range

    days = {}
    changed = 0
    existing = 0
    day = args.first
    while day <= args.last:
        path = args.output / "10min" / f"{day:%Y}" / f"{day:%m}" / f"{day:%Y-%m-%d}.json"
        payload = archive_day(day, station_ids, merged)
        existed_before = path.exists()
        if existed_before:
            existing += 1
            published = json.loads(path.read_text(encoding="utf-8"))
        else:
            published = payload
            if not args.dry_run:
                changed += int(write_json_if_changed(path, payload))
        counts = {station: sum(isinstance(value, (int, float)) for value in published["stations"][station]["values"])
                  for station in station_ids}
        days[day.isoformat()] = {"stationValidCounts": counts, "existingFile": existed_before}
        day += timedelta(days=1)

    report = {
        "schemaVersion": 1,
        "repository": REPOSITORY,
        "firstDate": args.first.isoformat(),
        "lastDate": args.last.isoformat(),
        "snapshots": snapshots,
        "days": days,
        "recordConflicts": dict(conflicts),
        "existingDaysUntouched": existing,
        "newDaysWritten": changed,
    }
    if not args.dry_run:
        write_json_if_changed(args.report, report)
    print(f"existing days untouched: {existing}; new days written: {changed}")
    for station in station_ids:
        counts = [info["stationValidCounts"][station] for info in days.values()]
        print(f"{station}: {sum(counts)}/{len(counts) * 144} valid; minimum day {min(counts)}; conflicts {conflicts[station]}")
        sparse = [(day, info["stationValidCounts"][station]) for day, info in days.items()
                  if info["stationValidCounts"][station] < 140]
        if sparse:
            print(f"  days below 140: {sparse}")


if __name__ == "__main__":
    main()
