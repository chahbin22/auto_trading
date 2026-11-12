
import argparse
import csv
from datetime import datetime
from math import isfinite
from pathlib import Path
from unicodedata import east_asian_width

from indicators import calculate_sma
from storage import FIELDS, candle_path, candle_time


MOMENTUM_PERIOD = 20
RSI_PERIOD = 14
FIRST_TRADE_INDEX = MOMENTUM_PERIOD + 1


def load_validated_candles(path, through):
    with path.open(encoding="utf-8", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if not set(FIELDS).issubset(reader.fieldnames or []):
            raise ValueError("CSV에 필요한 열이 없습니다.")

        candles = []
        previous_time = None
        for row in reader:
            try:
                timestamp = candle_time(row)
                if timestamp.utcoffset() is None:
                    raise ValueError("봉 시각에 시간대가 없습니다.")
                if previous_time is not None and timestamp <= previous_time:
                    raise ValueError("봉 시각이 중복됐거나 오름차순이 아닙니다.")
                previous_time = timestamp
                if timestamp > through:
                    continue

                opening, high, low, closing, volume = (
                    float(row[field]) for field in FIELDS[1:-1]
                )
                if not all(isfinite(value) for value in (opening, high, low, closing, volume)):
                    raise ValueError("가격 또는 거래량이 유한한 숫자가 아닙니다.")
                if low <= 0 or not low <= min(opening, closing) <= max(opening, closing) <= high:
                    raise ValueError("시가·고가·저가·종가 관계가 올바르지 않습니다.")
                if volume < 0 or row["currency"] != "USD":
                    raise ValueError("거래량 또는 통화가 올바르지 않습니다.")
            except (TypeError, ValueError) as error:
                raise ValueError(f"CSV {reader.line_num}행 | {error}") from error
            candles.append((timestamp, opening, closing))

    if len(candles) <= FIRST_TRADE_INDEX:
        raise ValueError("모멘텀 신호와 다음 봉 체결을 위해 일봉 22개 이상이 필요합니다.")
    return candles


def sma_signal(prices):
    return calculate_sma(prices, 5) > calculate_sma(prices, 20)


def momentum_signal(prices):
    return prices[-1] > prices[-1 - MOMENTUM_PERIOD]


def calculate_rsi(prices):
    changes = [current - previous for previous, current in zip(prices, prices[1:])]
    seed = changes[:RSI_PERIOD]
    average_gain = sum(max(change, 0) for change in seed) / RSI_PERIOD
    average_loss = sum(max(-change, 0) for change in seed) / RSI_PERIOD

    for change in changes[RSI_PERIOD:]:
        average_gain = (average_gain * (RSI_PERIOD - 1) + max(change, 0)) / RSI_PERIOD
        average_loss = (average_loss * (RSI_PERIOD - 1) + max(-change, 0)) / RSI_PERIOD

    if average_loss == 0:
        return 50.0 if average_gain == 0 else 100.0
    return 100 * average_gain / (average_gain + average_loss)


def rsi_signal(prices):
    rsi = calculate_rsi(prices)
    if rsi <= 30:
        return True
    if rsi >= 70:
        return False
    return None


def run_backtest(candles, initial_cash, fee_rate, signal=sma_signal, start_index=FIRST_TRADE_INDEX):
    cash = initial_cash
    shares = 0.0
    closing_prices = []
    peak = initial_cash
    max_drawdown = 0.0
    trades = []

    for index, (timestamp, opening, closing) in enumerate(candles):
        if index >= start_index:
            target_holding = signal(closing_prices)
            if target_holding is True and shares == 0:
                notional = cash / (1 + fee_rate)
                fee = cash - notional
                shares = notional / opening
                cash = 0.0
                trades.append((candles[index - 1][0], timestamp, "BUY", opening, shares, fee))
            elif target_holding is False and shares > 0:
                notional = shares * opening
                fee = notional * fee_rate
                cash = notional - fee
                trades.append((candles[index - 1][0], timestamp, "SELL", opening, shares, fee))
                shares = 0.0

        closing_prices.append(closing)
        equity = cash + shares * closing
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 1 - equity / peak)

    return equity, max_drawdown, trades, shares


def run_buy_and_hold(candles, initial_cash, fee_rate, start_index=FIRST_TRADE_INDEX):
    evaluation_candles = candles[start_index:]
    first_open = evaluation_candles[0][1]
    shares = initial_cash / (1 + fee_rate) / first_open
    peak = initial_cash
    max_drawdown = 0.0

    for _, _, closing in evaluation_candles:
        equity = shares * closing
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 1 - equity / peak)

    return equity, max_drawdown


def display_width(text):
    return sum(2 if east_asian_width(char) in ("W", "F") else 1 for char in text)


def print_comparison(initial_cash, results):
    headers = ("전략", "최종 평가액", "비용 차감 수익률", "최대 낙폭", "체결")
    rows = [headers]
    for name, equity, drawdown, trades in results:
        rows.append((
            name, f"${equity:,.2f}", f"{equity / initial_cash - 1:+.2%}",
            f"{drawdown:.2%}", f"{trades:,}건",
        ))

    widths = [max(map(display_width, column)) for column in zip(*rows)]
    for index, row in enumerate(rows):
        cells = []
        for column, (text, width) in enumerate(zip(row, widths)):
            padding = " " * (width - display_width(text))
            cells.append(text + padding if column == 0 else padding + text)
        print(" | ".join(cells))
        if index == 0:
            print("-+-".join("-" * width for width in widths))


TRADE_FIELDS = (
    "symbol", "status", "entry_signal_at", "entry_at", "exit_signal_at", "exit_at",
    "entry_price_usd", "exit_price_usd", "quantity", "entry_fee_usd", "exit_fee_usd",
    "net_pnl_usd", "net_return_pct", "profitable",
            )


def export_sma_trades(path, symbol, trades):
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=TRADE_FIELDS)
        writer.writeheader()

        for index in range(0, len(trades), 2):
            signal_at, filled_at, _, price, quantity, fee = trades[index]
            row = {
                "symbol": symbol,
                "status": "OPEN",
                "entry_signal_at": signal_at.isoformat(),
                "entry_at": filled_at.isoformat(),
                "entry_price_usd": price,
                "quantity": quantity,
                "entry_fee_usd": fee,
            }
            if index + 1 < len(trades):
                exit_signal, exit_at, _, exit_price, _, exit_fee = trades[index + 1]
                entry_cost = price * quantity + fee
                exit_proceeds = exit_price * quantity - exit_fee
                net_pnl = exit_proceeds - entry_cost
                row.update(
                    status="CLOSED",
                    exit_signal_at=exit_signal.isoformat(),
                    exit_at=exit_at.isoformat(),
                    exit_price_usd=exit_price,
                    exit_fee_usd=exit_fee,
                    net_pnl_usd=net_pnl,
                    net_return_pct=100 * net_pnl / entry_cost,
                    profitable=int(net_pnl > 0),
                )
            writer.writerow(row)


def main():
    parser = argparse.ArgumentParser(description="저장한 미국주식 일봉으로 네 전략 비교")
    parser.add_argument("symbol", choices=("IONQ", "RXRX", "BBAI"))
    parser.add_argument("--csv", type=Path, help="별도 CSV 경로")
    parser.add_argument("--from", dest="start", help="첫 체결 가능 봉의 ISO 8601 시각")
    parser.add_argument("--through", required=True, help="사용할 마지막 확정 봉의 ISO 8601 시각")
    parser.add_argument("--cash", type=float, default=10_000)
    parser.add_argument("--fee-bps", type=float, default=10, help="편도 비용, 1bp = 0.01%%")
    parser.add_argument("--export-trades", action="store_true", help="SMA 거래를 종목별 CSV로 저장")
    args = parser.parse_args()

    try:
        through = datetime.fromisoformat(args.through)
        if through.utcoffset() is None:
            raise ValueError("--through에 +09:00 같은 시간대 오프셋을 포함하세요.")
        if not isfinite(args.cash) or args.cash <= 0:
            raise ValueError("--cash는 양수여야 합니다.")
        if not 0 <= args.fee_bps < 10_000:
            raise ValueError("--fee-bps는 0 이상 10000 미만이어야 합니다.")
        candles = load_validated_candles(args.csv or candle_path(args.symbol), through)
        validated_count = len(candles)
        evaluation_start = FIRST_TRADE_INDEX
        if args.start is not None:
            start = datetime.fromisoformat(args.start)
            if start.utcoffset() is None:
                raise ValueError("--from에 +09:00 같은 시간대 오프셋을 포함하세요.")
            first_index = next(
                (index for index, candle in enumerate(candles) if candle[0] >= start),
                len(candles),
            )
            if first_index == len(candles):
                raise ValueError("--from 이후 평가할 일봉이 없습니다.")
            if first_index < FIRST_TRADE_INDEX:
                raise ValueError("--from 이전에 지표 계산용 일봉 21개가 필요합니다.")
            evaluation_start = first_index
    except (OSError, ValueError, csv.Error) as error:
        parser.exit(1, f"데이터 검사 실패 | {error}\n")

    fee_rate = args.fee_bps / 10_000
    equity, drawdown, trades, shares = run_backtest(
        candles, args.cash, fee_rate, start_index=evaluation_start
    )
    momentum_equity, momentum_drawdown, momentum_trades, _ = run_backtest(
        candles, args.cash, fee_rate, signal=momentum_signal, start_index=evaluation_start
    )
    rsi_equity, rsi_drawdown, rsi_trades, _ = run_backtest(
        candles, args.cash, fee_rate, signal=rsi_signal, start_index=evaluation_start
    )
    benchmark_equity, benchmark_drawdown = run_buy_and_hold(
        candles, args.cash, fee_rate, start_index=evaluation_start
    )
    print(f"{args.symbol} | 검증 통과 {validated_count}개 | 첫 체결 가능 봉 {candles[evaluation_start][0].isoformat()}")
    print(f"마지막 평가 봉 {candles[-1][0].isoformat()} | 편도 비용 {args.fee_bps:g}bp")
    print("SMA(5/20) 거래 내역")
    for signal, filled, side, price, quantity, fee in trades:
        print(f"{side:4} | 신호 {signal.isoformat()} | 체결 {filled.isoformat()}"
              f" | ${price:.4f} × {quantity:.6f}주 | 비용 ${fee:.2f}")
    print(f"최종 평가액 ${equity:,.2f} | 순수익률 {equity / args.cash - 1:.2%}"
          f" | 최대 낙폭 {drawdown:.2%} | 체결 {len(trades)}건")
    print(f"최종 보유 {shares:.6f}주")
    entry_count = sum(side == "BUY" for _, _, side, _, _, _ in trades)
    closed_count = len(trades) - entry_count
    print(f"SMA 진입 {entry_count}건 | 청산 완료 {closed_count}건"
          f" | 미청산 {entry_count - closed_count}건")
    print("\n전략 비교")
    print_comparison(
        args.cash,
        [
            ("SMA(5/20)", equity, drawdown, len(trades)),
            ("20거래일 모멘텀", momentum_equity, momentum_drawdown, len(momentum_trades)),
            ("RSI(14) 30/70", rsi_equity, rsi_drawdown, len(rsi_trades)),
            ("매수 후 보유", benchmark_equity, benchmark_drawdown, 1),
        ],
    )

    if args.export_trades:
        path = Path(f"{args.symbol}_sma_trades.csv")
        input_path = args.csv or candle_path(args.symbol)
        try:
            if path.resolve() == input_path.resolve() or (
                path.exists() and path.samefile(input_path)
            ):
                raise OSError("일봉 입력 CSV와 거래 출력 CSV가 같은 파일입니다.")
            export_sma_trades(path, args.symbol, trades)
        except OSError as error:
            parser.exit(1, f"거래 CSV 저장 실패 | {error}\n")
        print(f"SMA 거래 CSV 저장 | {path}")



if __name__ == "__main__":
    main()