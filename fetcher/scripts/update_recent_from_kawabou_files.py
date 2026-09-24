from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests


BASE_URL = "https://www.river.go.jp/kawabou/file"
FILES_URL = f"{BASE_URL}/files"
CURRENT_TIME_URL = f"{BASE_URL}/system/tmCrntTime.json"
JST = ZoneInfo("Asia/Tokyo")
USER_AGENT = "Mozilla/5.0 (compatible; KujiWaterLevelBot/1.0)"


@dataclass(frozen=True)
class StationTarget:
    id: str
    name: str
    ofc_cd: str
    itmknd_cd: str
    obs_cd: str
    data_dir: Path

    @property
    def obs_fcd(self) -> str:
        return build_obs_fcd(self.ofc_cd, self.itmknd_cd, self.obs_cd)


def build_obs_fcd(ofc_cd: str | int, itmknd_cd: str | int, obs_cd: str | int) -> str:
    return f"{int(ofc_cd):05d}{int(itmknd_cd):03d}{int(obs_cd):05d}"


def parse_observation_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y/%m/%d %H:%M").replace(tzinfo=JST)


def round_down_ten_minutes(value: datetime) -> datetime:
    return value.replace(minute=(value.minute // 10) * 10, second=0, microsecond=0)


def parse_value(value: Any, ccd: Any) -> float | None:
    try:
        parsed_ccd = int(ccd or 0)
    except (TypeError, ValueError):
        parsed_ccd = -1
    if parsed_ccd != 0:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > -9999 else None


def convert_records(values: list[dict[str, Any]], resolution: str | None = None) -> list[dict[str, Any]]:
    by_timestamp: dict[str, dict[str, Any]] = {}
    for item in values:
        observed_at = item.get("obsTime")
        if not isinstance(observed_at, str):
            continue
        try:
            timestamp = parse_observation_time(observed_at).strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            continue
        ccd = item.get("stgCcd")
        record = {
            "timestamp": timestamp,
            "value": parse_value(item.get("stg"), ccd),
            "flag": "" if ccd in (None, 0, "0") else f"ccd:{ccd}",
        }
        if resolution:
            record["resolution"] = resolution
        by_timestamp[timestamp] = record
    return [by_timestamp[key] for key in sorted(by_timestamp)]


def merge_records(existing: list[dict[str, Any]], fetched: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {record["timestamp"]: record for record in existing if record.get("timestamp")}
    merged.update({record["timestamp"]: record for record in fetched if record.get("timestamp")})
    return [merged[key] for key in sorted(merged)]


def clip_records(records: list[dict[str, Any]], latest: datetime, keep_hours: int) -> list[dict[str, Any]]:
    threshold = latest - timedelta(hours=keep_hours)
    return [
        record for record in records
        if datetime.fromisoformat(record["timestamp"]).replace(tzinfo=JST) >= threshold
    ]


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    records = payload.get("records")
    return records if isinstance(records, list) else []


def save_if_changed(path: Path, payload: dict[str, Any]) -> bool:
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if current.get("records") == payload.get("records"):
                print(f"unchanged {path} ({len(payload['records'])} records)")
                return False
        except (OSError, json.JSONDecodeError):
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)
    print(f"saved {path} ({len(payload['records'])} records)")
    return True


def load_config_targets(path: Path) -> list[StationTarget]:
    config = json.loads(path.read_text(encoding="utf-8"))
    targets = []
    for station in config.get("stations", []):
        source = station.get("kawabou") or {}
        if not source or not station.get("data_dir"):
            continue
        targets.append(StationTarget(
            id=station["id"],
            name=station.get("name", station["id"]),
            ofc_cd=str(source["ofc_cd"]),
            itmknd_cd=str(source.get("itmknd_cd", 4)),
            obs_cd=str(source["obs_cd"]),
            data_dir=Path(station["data_dir"]),
        ))
    return targets


def fetch_json(session: requests.Session, url: str, timeout: int) -> dict[str, Any]:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def update_target(
    target: StationTarget,
    session: requests.Session,
    folder_time: datetime,
    keep_10min_hours: int,
    keep_hourly_days: int,
    timeout: int,
) -> None:
    time_path = folder_time.strftime("%Y%m%d/%H%M")
    current_url = f"{FILES_URL}/tmlist/stg/{time_path}/{target.obs_fcd}.json"
    past_url = f"{FILES_URL}/tmlist/past/stg/{folder_time:%Y%m%d}/{target.obs_fcd}.json"
    current = fetch_json(session, current_url, timeout)
    try:
        past = fetch_json(session, past_url, timeout)
    except requests.RequestException:
        past = {"pastValues": []}

    ten_min = convert_records(current.get("min10Values") or [], "10min")
    hourly = convert_records([*(past.get("pastValues") or []), *(current.get("hrValues") or [])])
    latest = max(
        [parse_observation_time(current["obsValue"]["obsTime"]), folder_time],
        default=folder_time,
    )

    ten_min_path = target.data_dir / "recent_10min.json"
    hourly_path = target.data_dir / "recent_hourly.json"
    ten_min = clip_records(
        merge_records(load_records(ten_min_path), ten_min), latest, keep_10min_hours,
    )
    hourly = clip_records(
        merge_records(load_records(hourly_path), hourly), latest, keep_hourly_days * 24,
    )
    if not ten_min or not hourly:
        raise RuntimeError(f"no observations parsed for {target.id}")

    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    common_meta = {
        "source": "river_go_jp_kawabou_files",
        "station_code": target.obs_fcd,
        "station_name": target.name,
        "ofc_cd": target.ofc_cd,
        "itmknd_cd": target.itmknd_cd,
        "obs_cd": target.obs_cd,
        "current_source_url": current_url,
        "past_source_url": past_url,
        "last_fetch_utc": fetched_at,
        "notes": [
            "川の防災情報が画面表示に使用する公開JSONから取得。",
            "公開側の保持期間が短いため、取得済みデータをこのリポジトリで継続保存。",
        ],
    }
    save_if_changed(ten_min_path, {
        "meta": {
            **common_meta,
            "record_count": len(ten_min),
            "window_hours": keep_10min_hours,
            "dataset_start": ten_min[0]["timestamp"],
            "dataset_end": ten_min[-1]["timestamp"],
        },
        "records": ten_min,
    })
    save_if_changed(hourly_path, {
        "meta": {
            **common_meta,
            "record_count": len(hourly),
            "window_days": keep_hourly_days,
            "dataset_start": hourly[0]["timestamp"],
            "dataset_end": hourly[-1]["timestamp"],
        },
        "records": hourly,
    })


def main() -> None:
    parser = argparse.ArgumentParser(description="川の防災情報の公開JSONから水位を更新します。")
    parser.add_argument("--config", default="config/stations.json")
    parser.add_argument("--keep-10min-hours", type=int, default=168)
    parser.add_argument("--keep-hourly-days", type=int, default=45)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    targets = load_config_targets(Path(args.config))
    if not targets:
        print("no Kawabou file targets configured")
        return

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    current_time = fetch_json(session, CURRENT_TIME_URL, args.timeout)["crntObsTime"]
    folder_time = round_down_ten_minutes(parse_observation_time(current_time))
    for target in targets:
        update_target(
            target, session, folder_time,
            args.keep_10min_hours, args.keep_hourly_days, args.timeout,
        )


if __name__ == "__main__":
    main()
