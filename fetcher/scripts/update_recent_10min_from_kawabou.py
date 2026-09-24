from __future__ import annotations

import argparse
import csv
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests


HYDRO_BASE = "https://www1.river.go.jp"
HYDRO_WATER_DATA_URL = f"{HYDRO_BASE}/cgi-bin/DspWaterData.exe"
DEFAULT_OFC_CD = "21271"
DEFAULT_ITMKND_CD = "4"
DEFAULT_OBS_CD = "7"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class FetchResult:
    records: list[dict[str, Any]]
    station_meta: dict[str, str]
    source_url: str
    index_url: str
    errors: list[str]


@dataclass(frozen=True)
class StationTarget:
    id: str
    name: str
    hydrology_station_id: str
    ofc_cd: str | None
    itmknd_cd: str | None
    obs_cd: str | None
    output: Path


def parse_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= -9999:
        return None
    return parsed


def output_timestamp(value: datetime) -> str:
    return value.astimezone(JST).strftime("%Y-%m-%dT%H:%M")


def parse_output_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=JST)


def kind9_index_url(station_id: str) -> str:
    return f"{HYDRO_WATER_DATA_URL}?KIND=9&ID={station_id}"


def decode_response(response: requests.Response, encoding: str) -> str:
    return response.content.decode(encoding, errors="replace")


def fetch_text(session: requests.Session, url: str, timeout: int, encoding: str) -> str:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return decode_response(response, encoding)


def extract_first_dat_url(index_html: str) -> str | None:
    match = re.search(r'href="([^"]+\.dat)"', index_html, re.IGNORECASE)
    if not match:
        return None
    return urljoin(HYDRO_BASE, match.group(1))


def parse_station_meta_from_dat(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "," not in line:
            continue
        key, value = line.split(",", 1)
        key = key.strip()
        value = value.strip()
        if key in {"水系名", "河川名", "観測所名", "観測所記号"}:
            meta[key] = value
    return meta


def parse_hydro_timestamp(date_text: str, time_text: str) -> datetime:
    observed_date = datetime.strptime(date_text, "%Y/%m/%d").replace(tzinfo=JST)
    if time_text == "24:00":
        return observed_date + timedelta(days=1)
    hour, minute = [int(part) for part in time_text.split(":", 1)]
    return observed_date.replace(hour=hour, minute=minute)


def parse_hydro_10min_dat(text: str) -> list[dict[str, Any]]:
    records_by_ts: dict[str, dict[str, Any]] = {}
    reader = csv.reader(text.splitlines())
    for row in reader:
        if len(row) < 3 or not re.match(r"^\d{4}/\d{2}/\d{2}$", row[0]):
            continue
        value = parse_float(row[2])
        if value is None:
            continue

        observed_at = parse_hydro_timestamp(row[0], row[1])
        ts = output_timestamp(observed_at)
        records_by_ts[ts] = {
            "timestamp": ts,
            "value": value,
            "flag": row[3].strip() if len(row) >= 4 else "",
            "resolution": "10min",
        }

    return [records_by_ts[ts] for ts in sorted(records_by_ts)]


def clip_recent(records: list[dict[str, Any]], keep_hours: int) -> list[dict[str, Any]]:
    if not records:
        return []
    latest = parse_output_timestamp(records[-1]["timestamp"])
    threshold = latest - timedelta(hours=keep_hours)
    return [r for r in records if parse_output_timestamp(r["timestamp"]) >= threshold]


def load_existing_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    records = payload.get("records")
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict) and record.get("timestamp")]


def merge_observations(
    existing_records: list[dict[str, Any]],
    fetched_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge by observation time, preferring the latest fetch for duplicate timestamps."""
    records_by_ts: dict[str, dict[str, Any]] = {}
    for record in [*existing_records, *fetched_records]:
        timestamp = record.get("timestamp")
        if not isinstance(timestamp, str):
            continue
        try:
            parse_output_timestamp(timestamp)
        except ValueError:
            continue
        records_by_ts[timestamp] = record
    return [records_by_ts[timestamp] for timestamp in sorted(records_by_ts)]


def fetch_hydro_10min_dat(
    session: requests.Session,
    station_id: str,
    timeout: int,
) -> FetchResult:
    index_url = kind9_index_url(station_id)
    index_html = fetch_text(session, index_url, timeout, "euc_jp")
    dat_url = extract_first_dat_url(index_html)
    if not dat_url:
        raise RuntimeError(f"download .dat link not found in {index_url}")

    dat_text = fetch_text(session, dat_url, timeout, "cp932")
    records = parse_hydro_10min_dat(dat_text)
    if not records:
        raise RuntimeError(f"no 10-minute records parsed from {dat_url}")

    return FetchResult(
        records=records,
        station_meta=parse_station_meta_from_dat(dat_text),
        source_url=dat_url,
        index_url=index_url,
        errors=[],
    )


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def is_same_observation_payload(path: Path, payload: dict[str, Any]) -> bool:
    if not path.exists():
        return False
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    existing_meta = existing.get("meta") or {}
    next_meta = payload.get("meta") or {}
    stable_meta_keys = ("source", "station_code", "window_hours")
    return (
        existing.get("records") == payload.get("records")
        and all(existing_meta.get(key) == next_meta.get(key) for key in stable_meta_keys)
    )


def build_output_payload(
    records: list[dict[str, Any]],
    result: FetchResult,
    station_id: str,
    keep_hours: int,
) -> dict[str, Any]:
    return {
        "meta": {
            "source": "river_go_jp_hydro_kind9_dat",
            "station_code": station_id,
            "station_name": result.station_meta.get("観測所名"),
            "river_system": result.station_meta.get("水系名"),
            "river_name": result.station_meta.get("河川名"),
            "record_count": len(records),
            "window_hours": keep_hours,
            "dataset_start": records[0]["timestamp"],
            "dataset_end": records[-1]["timestamp"],
            "index_url": result.index_url,
            "source_url": result.source_url,
            "last_fetch_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "probe_errors": result.errors,
            "notes": [
                "水文水質データベースのリアルタイム10分水位一覧表からDLした dat ファイルで作成。",
                "既存の1時間データに直近10分観測値を重ねるためのファイルです。",
            ],
        },
        "records": records,
    }


def load_config_targets(config_path: Path) -> list[StationTarget]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    targets: list[StationTarget] = []
    for station in config.get("stations", []):
        ten_min = station.get("ten_min") or {}
        hourly = station.get("hourly") or {}
        data_dir = station.get("data_dir")
        if not data_dir:
            continue
        hydrology_station_id = ten_min.get("station_id") or hourly.get("station_id")
        if not hydrology_station_id:
            continue
        ofc_cd = ten_min.get("ofc_cd")
        itmknd_cd = ten_min.get("itmknd_cd")
        obs_cd = ten_min.get("obs_cd")
        targets.append(StationTarget(
            id=station["id"],
            name=station.get("name", station["id"]),
            hydrology_station_id=str(hydrology_station_id),
            ofc_cd=str(ofc_cd) if ofc_cd else None,
            itmknd_cd=str(itmknd_cd) if itmknd_cd else None,
            obs_cd=str(obs_cd) if obs_cd else None,
            output=Path(data_dir) / "recent_10min.json",
        ))
    return targets


def build_targets(args: argparse.Namespace) -> list[StationTarget]:
    if args.station_code:
        return [StationTarget(
            id=args.station_code,
            name=args.station_code,
            hydrology_station_id=args.station_code,
            ofc_cd=None,
            itmknd_cd=None,
            obs_cd=None,
            output=Path(args.output),
        )]

    config_path = Path(args.config)
    if config_path.exists():
        return load_config_targets(config_path)

    return [StationTarget(
        id="nukada",
        name="額田",
        hydrology_station_id="303011283322030",
        ofc_cd=DEFAULT_OFC_CD,
        itmknd_cd=DEFAULT_ITMKND_CD,
        obs_cd=DEFAULT_OBS_CD,
        output=Path(args.output),
    )]


def update_target(target: StationTarget, args: argparse.Namespace) -> None:
    if args.input:
        source_path = Path(args.input)
        dat_text = source_path.read_text(encoding=args.input_encoding)
        records = parse_hydro_10min_dat(dat_text)
        result = FetchResult(
            records=records,
            station_meta=parse_station_meta_from_dat(dat_text),
            source_url=f"local:{source_path}",
            index_url=kind9_index_url(target.hydrology_station_id),
            errors=[],
        )
    else:
        session = requests.Session()
        session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain,*/*",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        })
        result = fetch_hydro_10min_dat(session, target.hydrology_station_id, args.timeout)
        records = result.records

    # KIND=9 normally exposes only about 24 hours. Accumulate successive fetches
    # locally so a 48-hour comparison window can be retained.
    records = merge_observations(load_existing_records(target.output), records)
    records = clip_recent(records, args.keep_hours)
    if not records:
        raise RuntimeError(f"no 10-minute records parsed for {target.id}")

    payload = build_output_payload(
        records,
        result,
        target.hydrology_station_id,
        args.keep_hours,
    )
    if is_same_observation_payload(target.output, payload):
        print(f"unchanged {target.output} ({len(records)} records, latest {records[-1]['timestamp']})")
        return
    save_json(target.output, payload)
    print(f"saved {target.output} ({len(records)} records, latest {records[-1]['timestamp']})")


def main() -> None:
    parser = argparse.ArgumentParser(description="水文水質データベースの10分水位 dat から recent_10min.json を更新します。")
    parser.add_argument("--config", default="config/stations.json")
    parser.add_argument("--station-code", default=None, help="水文水質データベースの観測所記号")
    parser.add_argument("--output", default="data/recent_10min.json")
    parser.add_argument("--keep-hours", type=int, default=168)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--input", default=None, help="Local hydrology dat fixture for tests")
    parser.add_argument("--input-encoding", default="cp932")
    args = parser.parse_args()

    targets = build_targets(args)
    if not targets:
        raise SystemExit("no station targets configured")
    for target in targets:
        try:
            update_target(target, args)
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
