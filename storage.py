import csv
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile


DATA_DIR = Path(__file__).resolve().parent / "data"
FIELDS = (
    "timestamp", "openPrice", "highPrice", "lowPrice",
    "closePrice", "volume", "currency",
)


def candle_time(candle):
    return datetime.fromisoformat(candle["timestamp"])


def candle_path(symbol):
    return DATA_DIR / f"{symbol}_daily.csv"


def save_snapshot(symbol, candles):
    if not candles:
        raise ValueError("조회된 일봉이 없습니다.")
    ordered = sorted(candles, key=candle_time)
    rows = [{field: candle[field] for field in FIELDS} for candle in ordered]
    path = candle_path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as csv_file:
        temporary_path = Path(csv_file.name)
        try:
            writer = csv.DictWriter(csv_file, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            csv_file.close()
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)
    return path


def read_candles(symbol):
    with candle_path(symbol).open(encoding="utf-8", newline="") as csv_file:
        return sorted(csv.DictReader(csv_file), key=candle_time)
