import csv

with open("IONQ_daily.csv", newline="", encoding="utf-8") as csv_file:
    candles = list(csv.DictReader(csv_file))

if len(candles) < 20:
    raise SystemExit(f"일봉이 {len(candles)}개라 SMA(20)을 계산할 수 없습니다.")

closing_prices = [float(candle["closePrice"]) for candle in candles]

print(f"IONQ | 일봉 {len(candles)}개")
print(f"마지막 봉 시각 {candles[-1]['timestamp']}")

for period in (5, 10, 20):
    recent_prices = closing_prices[-period:]
    sma = sum(recent_prices) / period
    print(f"SMA({period}) {sma:.2f} USD")