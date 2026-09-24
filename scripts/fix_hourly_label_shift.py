"""一回限りの補正スクリプト。

時刻水位月表由来の1時間値取得スクリプト（`fetcher/scripts/update_recent_from_monthly_page.py`）
には、N列目（N=1..24、N時の正時水位）を (N-1):00 として保存していたバグがあった。
このスクリプトは、`fetcher/config/stations.json` で `hourly.station_id` を持つ地点（時刻水位
月表由来の地点）を対象に、`data/hourly/<station>/<year>.json` に保存済みの値を +1時間ずらし、
正しい時刻ラベルへ補正する。

補正方法:
- 対象地点ごとに全年ファイルを年順に連結した1本の配列とみなす。
- 配列全体を1要素ぶん後ろへずらす（新配列[i] = 旧配列[i-1]）。
- 各年末の最終要素は翌年ファイルの先頭に自然に移る（連結配列で扱うため）。
- 最古年の先頭（新配列の先頭）には移す元データが無いため null にする。
- 最新年の最終要素（連結配列の末尾）は移す先が無いため破棄されるが、
  これは常にまだ観測されていない未来枠（null）であることを確認したうえでのみ許可する。
  実データが入っていた場合は安全のため例外を送出して処理を中断する。

二重実行防止のため、補正済みの年ファイルには "labelShiftFixed": true を書き込み、
これが既に付いている地点（全年ファイルに付いている場合）はスキップする。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.archive_water_levels import year_minutes

STEP_MINUTES = 60


def target_station_ids(config_path: Path) -> list[str]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    ids: list[str] = []
    for station in config.get("stations", []):
        hourly = station.get("hourly") or {}
        if hourly.get("station_id"):
            ids.append(station["id"])
    return ids


def station_year_files(data_root: Path, station_id: str) -> list[Path]:
    directory = data_root / "hourly" / station_id
    if not directory.exists():
        return []
    return sorted(directory.glob("*.json"), key=lambda p: int(p.stem))


def write_json(path: Path, payload: dict) -> None:
    # 既存の data/hourly/*.json (archive_water_levels.write_json_if_changed) と同じ
    # コンパクト書式に合わせ、差分を最小化する。
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    path.write_text(encoded, encoding="utf-8")


def shift_station(data_root: Path, station_id: str) -> tuple[str, dict]:
    """対象地点1件を補正する。戻り値は (status, stats)。

    status: "fixed" | "skipped-already-fixed" | "skipped-no-data"
    """
    files = station_year_files(data_root, station_id)
    if not files:
        return "skipped-no-data", {}

    payloads = [json.loads(p.read_text(encoding="utf-8")) for p in files]
    years = [payload["year"] for payload in payloads]
    expected_years = list(range(years[0], years[0] + len(years)))
    if years != expected_years:
        raise ValueError(f"{station_id}: hourly year files are not contiguous: {years}")

    already_fixed = [bool(payload.get("labelShiftFixed")) for payload in payloads]
    if all(already_fixed):
        return "skipped-already-fixed", {}
    if any(already_fixed):
        raise ValueError(
            f"{station_id}: labelShiftFixed marker is inconsistent across years {years}"
        )

    expected_lengths = [year_minutes(year, STEP_MINUTES) for year in years]
    actual_lengths = [len(payload["values"]) for payload in payloads]
    if actual_lengths != expected_lengths:
        raise ValueError(
            f"{station_id}: unexpected values length per year: {actual_lengths} != {expected_lengths}"
        )

    global_values: list = []
    global_flags: dict[int, str] = {}
    offsets: list[int] = []
    offset = 0
    for payload, length in zip(payloads, actual_lengths):
        offsets.append(offset)
        global_values.extend(payload["values"])
        for key, flag in (payload.get("flags") or {}).items():
            global_flags[offset + int(key)] = flag
        offset += length

    total = len(global_values)
    if total == 0:
        return "skipped-no-data", {}

    if global_values[-1] is not None:
        raise ValueError(
            f"{station_id}: refusing to shift; the last hour of the latest year already "
            f"has a value ({global_values[-1]!r}), shifting would discard real data"
        )

    non_null_before = sum(1 for value in global_values if value is not None)
    value_multiset_before = sorted(value for value in global_values if value is not None)

    new_values = [None, *global_values[:-1]]
    new_flags: dict[int, str] = {}
    for index, flag in global_flags.items():
        if index == total - 1:
            # 旧配列の最終要素（常にnull、上で確認済み）と一緒に破棄する。
            continue
        new_flags[index + 1] = flag

    non_null_after = sum(1 for value in new_values if value is not None)
    value_multiset_after = sorted(value for value in new_values if value is not None)
    if non_null_after != non_null_before or value_multiset_after != value_multiset_before:
        raise ValueError(f"{station_id}: value integrity check failed after shifting")

    for payload, path, length, start in zip(payloads, files, actual_lengths, offsets):
        year_values = new_values[start:start + length]
        year_flags = {
            str(index - start): flag
            for index, flag in new_flags.items()
            if start <= index < start + length
        }
        payload["values"] = year_values
        if year_flags:
            payload["flags"] = year_flags
        elif "flags" in payload:
            del payload["flags"]
        payload["labelShiftFixed"] = True
        write_json(path, payload)

    stats = {
        "years": years,
        "non_null_before": non_null_before,
        "non_null_after": non_null_after,
    }
    return "fixed", stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="時刻水位月表由来の1時間値の時刻ラベルずれ（1時間早い）を補正します。"
    )
    parser.add_argument("--config", default="fetcher/config/stations.json")
    parser.add_argument("--data-root", default="data")
    args = parser.parse_args()

    config_path = Path(args.config)
    data_root = Path(args.data_root)
    station_ids = target_station_ids(config_path)
    if not station_ids:
        raise SystemExit("no station targets configured")

    for station_id in station_ids:
        status, stats = shift_station(data_root, station_id)
        if status == "fixed":
            print(
                f"fixed {station_id}: years={stats['years']} "
                f"non_null_before={stats['non_null_before']} non_null_after={stats['non_null_after']}"
            )
        else:
            print(f"{status} {station_id}")


if __name__ == "__main__":
    main()
