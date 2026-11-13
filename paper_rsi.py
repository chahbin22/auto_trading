"""Track RSI(14) 30/70 paper positions using confirmed daily candles."""

import argparse
import csv
import json
from datetime import datetime
from fcntl import LOCK_EX, flock
from math import isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile

from backtest import calculate_rsi, load_validated_candles
from storage import candle_path

SYMBOLS = ("IONQ", "RXRX", "BBAI")


def timestamp(value):
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.utcoffset() is not None:
            return stamp
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("시간대를 포함한 ISO 8601 시각을 입력하세요.")


def save_ledger(path, ledger):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            with file:
                json.dump(ledger, file, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def advance(ledger, candles):
    sessions = ledger["sessions"]
    checkpoint = datetime.fromisoformat(sessions[-1]["bar_at"]) if sessions else None
    start = datetime.fromisoformat(ledger["start"])
    fee_rate = ledger["fee_bps"] / 10_000
    indices = {}
    for symbol, rows in candles.items():
        stamps = {row[0]: i for i, row in enumerate(rows)}
        if checkpoint is not None:
            if checkpoint not in stamps:
                raise ValueError(f"{symbol} | 직전 처리 봉이 CSV에 없습니다.")
            index = stamps[checkpoint] + 1
        else:
            index = next((i for i, row in enumerate(rows) if row[0] >= start), len(rows))
        if index < 14:
            raise ValueError(f"{symbol} | 시작 전에 RSI 계산용 일봉이 부족합니다.")
        indices[symbol] = index

    dates = [row[0] for row in candles[SYMBOLS[0]][indices[SYMBOLS[0]]:]]
    for symbol in SYMBOLS[1:]:
        if dates != [row[0] for row in candles[symbol][indices[symbol]:]]:
            raise ValueError("세 종목의 새 일봉 시각이 일치하지 않습니다.")
    if not dates and not sessions:
        raise ValueError("시작일 이후 확정 일봉이 없습니다.")

    closes = {symbol: [row[2] for row in rows] for symbol, rows in candles.items()}
    for offset, stamp in enumerate(dates):
        day = {}
        for symbol in SYMBOLS:
            i = indices[symbol] + offset
            _, opening, closing = candles[symbol][i]
            previous = sessions[-1]["symbols"][symbol] if sessions else None
            original_shares = ledger["shares"][symbol]
            shares = previous["shares"] if previous else original_shares
            cash = previous["cash"] if previous else 0.0
            fill = None
            pending = previous["signal"] if previous else None
            if pending:
                side = pending["side"]
                if side == "SELL":
                    quantity = shares
                    notional = quantity * opening
                    fee = notional * fee_rate
                    cash += notional - fee
                    shares = 0.0
                else:
                    notional = cash / (1 + fee_rate)
                    fee = cash - notional
                    quantity = notional / opening
                    shares = quantity
                    cash = 0.0
                fill = {**pending, "fill_at": stamp.isoformat(), "price": opening,
                        "quantity": quantity, "fee": fee}

            rsi = calculate_rsi(closes[symbol][:i + 1])
            side = "SELL" if shares > 0 and rsi >= 70 else (
                "BUY" if cash > 0 and shares == 0 and rsi <= 30 else None
            )
            day[symbol] = {
                "close": closing, "rsi": rsi,
                "signal": {"side": side, "signal_at": stamp.isoformat()} if side else None,
                "fill": fill, "shares": shares, "cash": cash,
            }
            if not sessions:
                ledger["initial_equity"] += original_shares * opening

        sessions.append({
            "bar_at": stamp.isoformat(),
            "symbols": day,
            "paper_equity": sum(row["cash"] + row["shares"] * row["close"] for row in day.values()),
            "hold_equity": sum(ledger["shares"][symbol] * row["close"] for symbol, row in day.items()),
        })
    return len(dates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=timestamp, help="첫 모의 평가 봉의 시작 시각")
    parser.add_argument("--shares", type=float, nargs=3, metavar=("IONQ", "RXRX", "BBAI"))
    parser.add_argument("--through", type=timestamp, required=True, help="마지막 확정 일봉 시각")
    parser.add_argument("--ledger", type=Path, default=Path("paper_rsi_ledger.json"))
    args = parser.parse_args()

    path = args.ledger.resolve()
    inputs = {symbol: candle_path(symbol) for symbol in SYMBOLS}
    if any(path == source.resolve() for source in inputs.values()):
        parser.error("--ledger는 일봉 CSV와 다른 경로여야 합니다.")
    try:
        with path.with_name(path.name + ".lock").open("a") as lock:
            flock(lock, LOCK_EX)
            if path.exists():
                if args.start is not None or args.shares is not None:
                    raise ValueError("기존 장부를 이어갈 때는 --start와 --shares를 빼세요.")
                ledger = json.loads(path.read_text(encoding="utf-8"))
                if datetime.fromisoformat(ledger["sessions"][-1]["bar_at"]) > args.through:
                    raise ValueError("--through가 직전 처리 봉보다 이전입니다.")
            else:
                if args.start is None or args.shares is None:
                    raise ValueError("첫 실행에는 --start와 --shares가 필요합니다.")
                if any(not isfinite(n) or n <= 0 for n in args.shares):
                    raise ValueError("보유 수량은 유한한 양수여야 합니다.")
                ledger = {"start": args.start.isoformat(), "shares": dict(zip(SYMBOLS, args.shares)),
                          "fee_bps": 10, "initial_equity": 0.0, "sessions": []}

            candles = {symbol: load_validated_candles(source, args.through)
                       for symbol, source in inputs.items()}
            count = advance(ledger, candles)
            if count:
                save_ledger(path, ledger)
    except (OSError, ValueError, csv.Error) as error:
        parser.exit(1, f"모의 장부 처리 실패 | {error}\n")

    last = ledger["sessions"][-1]
    print(f"{last['bar_at']} | 새 일봉 {count}개 | 누적 {len(ledger['sessions'])}거래일")
    for symbol, row in last["symbols"].items():
        pending = row["signal"]["side"] if row["signal"] else "없음"
        print(f"{symbol} | RSI {row['rsi']:.2f} | {row['shares']:.6f}주"
              f" | 현금 ${row['cash']:,.2f} | 다음 시가 주문 {pending}")
    for key, label in (("paper_equity", "RSI 모의 계좌"), ("hold_equity", "계속 보유")):
        value = last[key]
        print(f"{label} | ${value:,.2f} | {value / ledger['initial_equity'] - 1:+.2%}")
    print(f"장부 | {path}")


if __name__ == "__main__":
    main()
