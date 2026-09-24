from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import requests

BASE_URL = "https://www1.river.go.jp"
MONTHLY_URL_TEMPLATE = (
    "https://www1.river.go.jp/cgi-bin/DspWaterData.exe?KIND=2&ID={station_id}"
    "&BGNDATE={bgn_date}&ENDDATE={end_date}&KAWABOU=NO"
)
DAT_LINK_RE = re.compile(r'href=["\'](?P<href>/dat/dload/download/[^"\']+\.dat)["\']', re.IGNORECASE)
TIME_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")


@dataclass(frozen=True)
class Record:
    timestamp: str
    value: float | None
    flag: str = ""


@dataclass(frozen=True)
class StationTarget:
    id: str
    name: str
    station_id: str
    output: Path


def decode_html(content: bytes) -> str:
    for enc in ("cp932", "shift_jis", "utf-8", "euc_jp", "latin1"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="ignore")


def monthly_page_url(station_id: str, year: int, month: int) -> str:
    bgn_date = f"{year:04d}{month:02d}01"
    end_date = f"{year:04d}1231"
    return MONTHLY_URL_TEMPLATE.format(station_id=station_id, bgn_date=bgn_date, end_date=end_date)


def month_iter(today: date) -> list[tuple[int, int]]:
    current = date(today.year, today.month, 1)
    if current.month == 1:
        prev = date(current.year - 1, 12, 1)
    else:
        prev = date(current.year, current.month - 1, 1)
    return [(prev.year, prev.month), (current.year, current.month)]


def extract_dat_link(html: str) -> str | None:
    m = DAT_LINK_RE.search(html)
    return m.group("href") if m else None


def parse_monthly_dat(text: str) -> list[Record]:
    records: list[Record] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not TIME_RE.match(line.split(",", 1)[0].strip()):
            continue
        row = next(csv.reader([raw_line]))
        day = row[0].strip()
        for hour in range(24):
            value_idx = 1 + hour * 2
            flag_idx = value_idx + 1
            if value_idx >= len(row):
                break
            value_text = row[value_idx].strip()
            flag = row[flag_idx].strip() if flag_idx < len(row) else ""
            ts = f"{day}T{hour:02d}:00".replace("/", "-")
            if value_text in {"", "$", "#", "-"}:
                records.append(Record(timestamp=ts, value=None, flag=flag or value_text))
                continue
            try:
                value = float(value_text)
            except ValueError:
                records.append(Record(timestamp=ts, value=None, flag=flag or value_text))
                continue
            if flag in {"-", "$", "#"}:
                records.append(Record(timestamp=ts, value=None, flag=flag))
                continue
            if value <= -9999:
                records.append(Record(timestamp=ts, value=None, flag=flag or 'missing'))
                continue
            records.append(Record(timestamp=ts, value=value, flag=flag))
    return records


def load_json(path: Path) -> dict:
    if not path.exists():
        return {"meta": {}, "records": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def clip_recent(records: Iterable[dict], keep_days: int, now_ts: datetime) -> list[dict]:
    threshold = now_ts - timedelta(days=keep_days)
    clipped = []
    for r in records:
        ts = datetime.fromisoformat(r["timestamp"])
        if ts >= threshold:
            clipped.append(r)
    clipped.sort(key=lambda r: r["timestamp"])
    return clipped


def fetch_month_records(session: requests.Session, station_id: str, year: int, month: int, timeout: int) -> list[Record]:
    page_url = monthly_page_url(station_id, year, month)
    page_resp = session.get(page_url, timeout=timeout)
    page_resp.raise_for_status()
    html = decode_html(page_resp.content)
    href = extract_dat_link(html)
    if not href:
        raise RuntimeError(f"dat link not found in monthly page: {page_url}")
    dat_url = urljoin(BASE_URL, href)
    dat_resp = session.get(dat_url, timeout=timeout)
    dat_resp.raise_for_status()
    text = decode_html(dat_resp.content)
    return parse_monthly_dat(text)


def load_config_targets(config_path: Path) -> list[StationTarget]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    targets: list[StationTarget] = []
    for station in config.get("stations", []):
        hourly = station.get("hourly") or {}
        station_id = hourly.get("station_id")
        data_dir = station.get("data_dir")
        if not station_id or not data_dir:
            continue
        targets.append(StationTarget(
            id=station["id"],
            name=station.get("name", station["id"]),
            station_id=str(station_id),
            output=Path(data_dir) / "recent_hourly.json",
        ))
    return targets


def build_targets(args: argparse.Namespace) -> list[StationTarget]:
    if args.station_id:
        return [StationTarget(
            id=args.station_id,
            name=args.station_id,
            station_id=args.station_id,
            output=Path(args.output),
        )]

    config_path = Path(args.config)
    if config_path.exists():
        return load_config_targets(config_path)

    return [StationTarget(
        id="nukada",
        name="額田",
        station_id="303011283322030",
        output=Path(args.output),
    )]


def update_target(
    target: StationTarget,
    session: requests.Session,
    today: date,
    keep_days: int,
    timeout: int,
) -> None:
    fetched: list[dict] = []
    for year, month in month_iter(today):
        month_records = fetch_month_records(session, target.station_id, year, month, timeout)
        for r in month_records:
            fetched.append({"timestamp": r.timestamp, "value": r.value, "flag": r.flag})

    now_ts = datetime.now()
    latest_data = clip_recent(fetched, keep_days, now_ts)
    payload = {
        "meta": {
            "source": "monthly_page_dat",
            "station_id": target.station_id,
            "station_name": target.name,
            "record_count": len(latest_data),
            "window_days": keep_days,
            "last_fetch_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "months_fetched": [f"{y:04d}-{m:02d}" for y, m in month_iter(today)],
        },
        "records": latest_data,
    }
    save_json(target.output, payload)
    print(f"saved {target.output} ({len(latest_data)} records)")


def main() -> None:
    parser = argparse.ArgumentParser(description="時刻水位月表から直近の1時間データを更新します。")
    parser.add_argument("--config", default="config/stations.json")
    parser.add_argument("--station-id", default=None)
    parser.add_argument("--output", default="data/recent_hourly.json")
    parser.add_argument("--keep-days", type=int, default=45)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--today", default=None, help="YYYY-MM-DD。テスト用")
    args = parser.parse_args()

    today = date.fromisoformat(args.today) if args.today else datetime.now().date()

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; KujiWaterLevelBot/1.0)",
    })

    targets = build_targets(args)
    if not targets:
        raise SystemExit("no station targets configured")
    for target in targets:
        update_target(target, session, today, args.keep_days, args.timeout)


if __name__ == "__main__":
    main()
