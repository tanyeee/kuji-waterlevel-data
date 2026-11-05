"""Validate and preserve a manually collected batch of Nukada readings."""
import argparse
import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

SOURCE = "https://www.river.go.jp/kawabou/mb/tm?ofcCd=21271&itmkndCd=4&obsCd=7"
LIVE = "https://tanyeee.github.io/kuji-waterlevel-data/live/stations/nukada/recent_10min.json"
JST = timezone(timedelta(hours=9))


def parse_payload(text):
    if len(text.encode()) > 50000:
        raise ValueError("Import exceeds 50 KB")
    payload = json.loads(text)
    if (payload["stationId"] != "nukada" or payload["sourceUrl"] != SOURCE
            or payload["resolution"] not in ("10min", "hourly")
            or not isinstance(payload["raw"], str) or not payload["raw"].strip()):
        raise ValueError("Invalid station, source, resolution or original text")
    reference = date.fromisoformat(payload["referenceDate"])
    records = payload["records"]
    if not isinstance(records, list) or not records:
        raise ValueError("No records")
    previous = ""
    valid_count = 0
    for row in records:
        timestamp, value, flag = row["timestamp"], row["value"], row["flag"]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", timestamp):
            raise ValueError("Invalid timestamp")
        observed = datetime.fromisoformat(timestamp).replace(tzinfo=JST)
        offset = reference - observed.date()
        if (timestamp <= previous or observed > datetime.now(JST)
                or not -1 <= offset.days <= 7 or row["resolution"] != payload["resolution"]
                or observed.minute % (10 if payload["resolution"] == "10min" else 60)):
            raise ValueError(f"Invalid or unordered observation at {timestamp}")
        if value is None:
            if flag not in ("-", "--", "$", "#"):
                raise ValueError("Unknown missing value flag")
        elif (type(value) not in (int, float) or not math.isfinite(value)
              or value <= -9999 or flag != ""):
            raise ValueError("Invalid water level")
        else:
            valid_count += 1
        previous = timestamp
    if not valid_count:
        raise ValueError("No valid observations")
    return payload


def hourly_values(root, rows):
    archives = {}
    for row in rows:
        moment = datetime.fromisoformat(row["timestamp"])
        if moment.minute:
            continue
        if moment.year not in archives:
            path = root / "hourly" / "nukada" / f"{moment.year}.json"
            archives[moment.year] = json.loads(path.read_text()) if path.exists() else None
        archive = archives[moment.year]
        index = (moment - datetime(moment.year, 1, 1)) // timedelta(hours=1)
        if archive and archive.get("flags", {}).get(str(index)) not in ("-", "$", "#"):
            yield row["timestamp"], archive["values"][index]


def stage_import(root, payload, live):
    station = root / "manual/stations/nukada.json"
    batches = root / "manual/imports"
    old = json.loads(station.read_text())["records"] if station.exists() else []
    records = {row["timestamp"]: row for row in old}
    official = dict(hourly_values(root, payload["records"]))
    published = {row["timestamp"]: row["value"] for row in live
                 if type(row.get("value")) in (int, float)
                 and row.get("flag") not in ("-", "$", "#")}
    for row in payload["records"]:
        timestamp, value = row["timestamp"], row["value"]
        existing = records.get(timestamp, {}).get("value")
        if value is not None and any(other is not None and other != value
                                     for other in (official.get(timestamp), published.get(timestamp), existing)):
            raise ValueError(f"Existing water level conflicts at {timestamp}")
        if timestamp not in records or (existing is None and value is not None):
            records[timestamp] = row
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    batch = batches / f"{digest}.json"
    return {
        station: {"stationId": "nukada", "records": [records[key] for key in sorted(records)]},
        **({batch: {**payload, "sha256": digest, "importedAt": datetime.now(JST).isoformat(timespec="seconds")}}
           if not batch.exists() else {}),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--live-file", type=Path, help="Local fixture for a dry run")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    payload = parse_payload(args.input.read_text(encoding="utf-8"))
    if args.live_file:
        if args.write:
            parser.error("--live-file is for dry runs only")
        live = json.loads(args.live_file.read_text())
    else:
        with urlopen(LIVE, timeout=30) as response:
            live = json.load(response)
    changes = stage_import(args.data, payload, live["records"])
    for path, content in changes.items():
        text = json.dumps(content, ensure_ascii=False, separators=(",", ":")) + "\n"
        if args.write and (not path.exists() or path.read_text() != text):
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(path)
    print(f"Validated {len(payload['records'])} observations")


if __name__ == "__main__":
    main()
