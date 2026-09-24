#!/usr/bin/env python3
"""Prepare and package rolling river readings for commit-free Pages delivery."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

LIVE_BASE = "https://tanyeee.github.io/kuji-waterlevel-data/live/stations"
STATION_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def stations(viewer: Path) -> list[tuple[str, Path]]:
    config = json.loads((viewer / "config/stations.json").read_text(encoding="utf-8"))
    result = []
    for station in config["stations"]:
        station_id = station["id"]
        if not STATION_ID.fullmatch(station_id):
            raise ValueError(f"Invalid station id: {station_id}")
        result.append((station_id, viewer / station["data_dir"] / "recent_10min.json"))
    return result


def checked_records(payload: dict, label: str) -> list[dict]:
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError(f"Missing records in {label}")
    previous = ""
    for record in records:
        timestamp = record.get("timestamp") if isinstance(record, dict) else None
        if not isinstance(timestamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", timestamp):
            raise ValueError(f"Invalid timestamp in {label}")
        datetime.fromisoformat(timestamp)
        if timestamp <= previous:
            raise ValueError(f"Unsorted or duplicate records in {label}")
        previous = timestamp
    return records


def read_live(url: str, *, bootstrap: bool) -> dict | None:
    try:
        with urlopen(Request(url, headers={"User-Agent": "kuji-waterlevel-live-builder"}), timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        if bootstrap and error.code == 404:
            return None
        raise


def prepare(viewer: Path, state_path: Path, live_base: str, bootstrap: bool) -> dict[str, str]:
    latest_by_station = {}
    for station_id, path in stations(viewer):
        # fetcher/data starts empty on every run (nothing is committed there), so the
        # local file normally will not exist yet: treat that as "no local records"
        # rather than failing, and fall back to Pages as the source of truth.
        if path.exists():
            old = json.loads(path.read_text(encoding="utf-8"))
            old_records = checked_records(old, f"old/{station_id}")
        else:
            old = None
            old_records = []

        live_url = f"{live_base.rstrip('/')}/{station_id}/recent_10min.json"
        live = read_live(live_url, bootstrap=bootstrap)
        if not bootstrap and live is None:
            raise ValueError(f"Live state missing for {station_id}")
        if live:
            live_records = checked_records(live, f"live/{station_id}")
            if old is not None:
                old_code = old.get("meta", {}).get("station_code")
                live_code = live.get("meta", {}).get("station_code")
                if old_code != live_code:
                    raise ValueError(f"Station code mismatch for {station_id}")
        else:
            live_records = []

        # Existing live data wins on duplicate timestamps. The local copy is only
        # a bootstrap fallback (or absent) and is never pushed back into Git.
        merged = {record["timestamp"]: record for record in old_records}
        merged.update({record["timestamp"]: record for record in live_records})
        if not merged:
            # Bootstrap and neither a local file nor Pages has this station yet.
            # Leave it for the fetch step to create from scratch; record an empty
            # sentinel so package()'s regression check has a key to compare against.
            latest_by_station[station_id] = ""
            continue

        records = [merged[key] for key in sorted(merged)]
        latest_by_station[station_id] = records[-1]["timestamp"]
        template = old if old is not None else live
        payload = {**template, "meta": {**template.get("meta", {}), "record_count": len(records),
                                    "dataset_start": records[0]["timestamp"],
                                    "dataset_end": records[-1]["timestamp"]}, "records": records}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(latest_by_station, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return latest_by_station


def package(viewer: Path, state_path: Path, output: Path) -> dict[str, str]:
    before = json.loads(state_path.read_text(encoding="utf-8"))
    latest_by_station = {}
    for station_id, path in stations(viewer):
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = checked_records(payload, f"updated/{station_id}")
        latest = records[-1]["timestamp"]
        if latest < before[station_id]:
            raise ValueError(f"Latest observation regressed for {station_id}")
        latest_by_station[station_id] = latest
        destination = output / "live/stations" / station_id / "recent_10min.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    (output / "live").mkdir(parents=True, exist_ok=True)
    manifest = {"schemaVersion": 1,
                "generatedAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "stations": latest_by_station}
    (output / "live/manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    (output / "index.html").write_text(
        '<!doctype html><html lang="ja"><meta charset="utf-8"><title>河川水位データ</title>'
        '<p>久慈川・那珂川水系の<a href="live/manifest.json">最新水位データ</a>を公開しています。</p></html>\n',
        encoding="utf-8",
    )
    return latest_by_station


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "package"])
    parser.add_argument("--viewer", type=Path, required=True)
    parser.add_argument("--state", type=Path, default=Path("live-state.json"))
    parser.add_argument("--output", type=Path, default=Path("_site"))
    parser.add_argument("--live-base", default=LIVE_BASE)
    parser.add_argument("--bootstrap", action="store_true", help="Allow an initial 404 before Pages exists")
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.viewer, args.state, args.live_base, args.bootstrap)
    else:
        result = package(args.viewer, args.state, args.output)
    print(f"{args.command}: {len(result)} stations, latest range {min(result.values())} .. {max(result.values())}")


if __name__ == "__main__":
    main()
