import argparse
import sys
from datetime import datetime

import requests

from indicators import calculate_sma
from storage import candle_time, read_candles, save_snapshot
from toss_client import get_daily_candles, issue_access_token


SYMBOLS = ("IONQ", "RXRX", "BBAI")


def show_smas(symbol, candles, through=None):
    if through is not None:
        candles = [candle for candle in candles if candle_time(candle) <= through]
    if len(candles) < 20:
        print(f"{symbol} | 사용 가능한 일봉 {len(candles)}개. SMA(20)은 최소 20개 필요.")
        return

    prices = [float(candle["closePrice"]) for candle in candles]
    latest = candles[-1]
    averages = " | ".join(
        f"SMA({period}) {calculate_sma(prices, period):.2f}"
        for period in (5, 10, 20)
    )
    print(f"{symbol} | 기준 봉 {latest['timestamp']} | 가격 {prices[-1]:.2f} "
          f"{latest['currency']} | {averages}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("collect", "inspect"))
    parser.add_argument("symbols", nargs="*")
    parser.add_argument("--pages", type=int, default=2)
    parser.add_argument("--through", help="포함할 마지막 봉의 ISO 8601 시각")
    args = parser.parse_args()
    symbols = tuple(dict.fromkeys(symbol.upper() for symbol in args.symbols)) or SYMBOLS
    if any(symbol not in SYMBOLS for symbol in symbols):
        parser.error("지원 종목은 IONQ, RXRX, BBAI입니다.")
    if not 1 <= args.pages <= 5:
        parser.error("--pages는 1~5 사이여야 합니다.")

    through = None
    if args.through:
        try:
            through = datetime.fromisoformat(args.through)
        except ValueError:
            parser.error("--through에 ISO 8601 시각을 입력하세요.")
        if through.utcoffset() is None:
            parser.error("--through 시각에 +09:00 같은 시간대 오프셋을 포함하세요.")

    access_token = None
    if args.mode == "collect":
        try:
            access_token = issue_access_token()
        except (requests.RequestException, ValueError, KeyError) as error:
            print(f"토큰 발급 실패 | {error}", file=sys.stderr)
            return 1

    failed = False
    for symbol in symbols:
        try:
            if args.mode == "collect":
                candles = get_daily_candles(symbol, access_token, args.pages)
                path = save_snapshot(symbol, candles)
                print(f"{symbol} | 일봉 {len(candles)}개 저장 | {path}")
            show_smas(symbol, read_candles(symbol), through)
        except (requests.RequestException, OSError, ValueError, KeyError) as error:
            print(f"{symbol} | 처리 실패 | {error}", file=sys.stderr)
            failed = True
            if isinstance(error, requests.HTTPError) and error.response.status_code == 429:
                print("호출 한도에 도달했습니다. 잠시 뒤 다시 실행하세요.", file=sys.stderr)
                break
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
