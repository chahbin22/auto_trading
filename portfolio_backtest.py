"""Compare portfolio strategies using the combined equity of three USD allocations."""

import argparse
import csv
from datetime import datetime, timedelta
from math import isclose, isfinite

from backtest import (
    FIRST_TRADE_INDEX,
    load_validated_candles,
    print_comparison,
    model_predictions,
    ridge_predictions,
    rsi_signal,
    run_backtest,
)
from storage import candle_path

SYMBOLS = ("IONQ", "RXRX", "BBAI")
STRATEGIES = ("매수 후 보유", "RSI 현금 출발", "RSI 보유 출발", "Ridge", "Ridge 보유 출발", "GBRT 보유 출발")


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is not None:
            return parsed
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("시간대를 포함한 ISO 8601 시각을 입력하세요.")


def equity_curve(candles, initial_cash, trades, *, initial_shares=0.0):
    """Value one allocation at each close using the engine's actual fills."""
    fills = {trade[1]: trade for trade in trades}
    cash = 0.0 if initial_shares else initial_cash
    shares = initial_shares
    for stamp, _, closing in candles:
        if stamp in fills:
            _, _, side, price, quantity, fee = fills[stamp]
            if side == "BUY":
                cash = 0.0
                shares = quantity
            else:
                cash = price * quantity - fee
                shares = 0.0
        yield cash + shares * closing


def summarize(initial_cash, curves, stamps):
    peak = initial_cash
    peak_stamp = None
    drawdown = 0.0
    equity = initial_cash
    interval = None
    for stamp, *values in zip(stamps, *curves, strict=True):
        equity = sum(values)
        if equity > peak:
            peak, peak_stamp = equity, stamp
        decline = 1 - equity / peak
        if decline > drawdown:
            drawdown = decline
            interval = (peak_stamp, stamp)
    return equity, drawdown, interval


def annual_predictions(candles, start, timezone):
    ridge = {}
    boosted = {}
    evaluation_start = start
    while evaluation_start < len(candles):
        year = candles[evaluation_start][0].astimezone(timezone).year
        validation_start = next(
            i for i, candle in enumerate(candles)
            if candle[0].astimezone(timezone).year >= year - 1
        )
        end = next(
            (i for i in range(evaluation_start, len(candles))
             if candles[i][0].astimezone(timezone).year > year),
            len(candles),
        )
        print(f"{year}년 예측 | {year - 1}년 검증")
        window = candles[:end]
        ridge.update(ridge_predictions(window, validation_start, evaluation_start))
        boosted.update(model_predictions(window, validation_start, evaluation_start, boosted=True))
        evaluation_start = end
    return ridge, boosted


def compare_portfolio(args):
    names = (STRATEGIES[0], STRATEGIES[2]) if args.rsi_only else STRATEGIES
    curves = {name: [] for name in names}
    fill_counts = dict.fromkeys(names, 0)
    first_sales = {}
    fee_rate = args.fee_bps / 10_000
    evaluation_stamps = None

    for symbol, weight in zip(SYMBOLS, args.weights):
        if weight == 0:
            continue
        raw_candles = load_validated_candles(
            candle_path(symbol), args.through, include_volume=not args.rsi_only
        )
        candles = [candle[:3] for candle in raw_candles]
        start = next(
            (i for i, candle in enumerate(candles) if candle[0] >= args.start),
            len(candles),
        )
        if start < FIRST_TRADE_INDEX or len(candles) - start < 2:
            raise ValueError(f"{symbol} | 시작 전 일봉 21개와 평가 일봉 2개 이상이 필요합니다.")

        evaluation = candles[start:]
        stamps = [candle[0] for candle in evaluation]
        if evaluation_stamps is None:
            evaluation_stamps = stamps
        elif stamps != evaluation_stamps:
            raise ValueError(f"{symbol} | 평가 구간의 일봉 시각이 다른 종목과 일치하지 않습니다.")

        allocation = args.cash * weight / sum(args.weights)
        print(f"\n{symbol} | 시작 배정액 ${allocation:,.2f}")
        starting_shares = allocation / evaluation[0][1]

        def hold_signal(_prices):
            return True

        strategies = {
            STRATEGIES[0]: (hold_signal, starting_shares),
            STRATEGIES[1]: (rsi_signal, 0.0),
            STRATEGIES[2]: (rsi_signal, starting_shares),
        }
        if not args.rsi_only:
            if args.continuous_from is not None:
                predictions, boosted = annual_predictions(raw_candles, start, args.through.tzinfo)
            else:
                validation = next(i for i, candle in enumerate(candles)
                                  if candle[0] >= args.validation_from)
                predictions = ridge_predictions(raw_candles, validation, start)
                boosted = model_predictions(raw_candles, validation, start, boosted=True)

            def ridge_signal(prices):
                return predictions[len(prices) - 1] > 0

            def boosted_signal(prices):
                return boosted[len(prices) - 1] > 0

            strategies.update({
                STRATEGIES[3]: (ridge_signal, 0.0),
                STRATEGIES[4]: (ridge_signal, starting_shares),
                STRATEGIES[5]: (boosted_signal, starting_shares),
            })

        for name in names:
            signal, shares = strategies[name]
            _, _, trades, _ = run_backtest(
                candles, allocation, fee_rate, signal, start, initial_shares=shares
            )
            curves[name].append(list(equity_curve(
                evaluation, allocation, trades, initial_shares=shares
            )))
            fill_counts[name] += len(trades)
            if shares and name != STRATEGIES[0]:
                first_sales[name, symbol] = next(
                    (trade[1] for trade in trades if trade[2] == "SELL"), None
                )

    print(f"\n평가 기간 {evaluation_stamps[0].isoformat()} ~ {evaluation_stamps[-1].isoformat()}"
          f" | {len(evaluation_stamps)}거래일 | 편도 비용 {args.fee_bps:g}bp")
    results = {
        name: summarize(args.cash, curves[name], evaluation_stamps)
        for name in names
    }
    print_comparison(args.cash, [
        (name, equity, drawdown, fill_counts[name])
        for name, (equity, drawdown, _) in results.items()
    ])

    print("\n최대 낙폭 구간 (고점 → 저점)")
    for name, (_, drawdown, interval) in results.items():
        if interval is None:
            print(f"{name} | 낙폭 없음")
            continue
        peak, trough = interval
        peak_label = peak.isoformat() if peak else "첫 평가 봉 시가의 시작 잔액"
        print(f"{name} | {peak_label} → {trough.isoformat()} | {drawdown:.2%}")

    print("\n보유 출발 전략 | 종목별 첫 매도 체결")
    for (name, symbol), sold_at in first_sales.items():
        print(f"{name} | {symbol} | {sold_at.isoformat() if sold_at else '평가 기간 중 매도 없음'}")

    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", nargs=3, type=float, required=True,
                        metavar=("IONQ", "RXRX", "BBAI"), help="종목별 시작 비중(%%), 합계 100")
    period = parser.add_mutually_exclusive_group(required=True)
    period.add_argument("--from", dest="start", type=timestamp)
    period.add_argument("--continuous-from", type=timestamp,
                        help="시작 잔액을 유지하고 연초마다 모델을 갱신")
    period.add_argument("--years", nargs="+", type=int, help="독립적으로 평가할 연도")
    parser.add_argument("--through", required=True, type=timestamp)
    parser.add_argument("--ridge-validation-from", dest="validation_from",
                        type=timestamp)
    parser.add_argument("--cash", type=float, default=10_000)
    parser.add_argument("--fee-bps", type=float, default=10)
    parser.add_argument("--rsi-only", action="store_true", help="계속 보유와 RSI 보유 출발만 비교")
    args = parser.parse_args()

    if (any(not 0 <= weight <= 100 for weight in args.weights)
            or not isclose(sum(args.weights), 100, abs_tol=1e-8)):
        parser.error("--weights에는 합계가 100인 비음수 비중 3개를 입력하세요.")
    if not isfinite(args.cash) or args.cash <= 0 or not 0 <= args.fee_bps < 10_000:
        parser.error("--cash는 유한한 양수, --fee-bps는 0 이상 10000 미만이어야 합니다.")
    if args.start is not None:
        if args.start >= args.through or (not args.rsi_only and (
            args.validation_from is None or args.validation_from >= args.start
        )):
            parser.error("검증 시작, 평가 시작, 평가 종료 시각을 순서대로 지정하세요.")
    elif args.continuous_from is not None:
        if args.validation_from is not None or args.continuous_from >= args.through:
            parser.error("--continuous-from은 종료일보다 앞서야 합니다. 검증은 직전 1년입니다.")
        args.start = args.continuous_from
        start_year = args.start.astimezone(args.through.tzinfo).year
        if start_year < 2:
            parser.error("--continuous-from에는 2년 이후의 시각을 입력하세요.")
        args.validation_from = datetime(start_year - 1, 1, 1, tzinfo=args.through.tzinfo)
    elif args.validation_from is not None or any(
        not 2 <= year <= min(9998, args.through.year) for year in args.years
    ):
        parser.error("--years에는 종료일 이내의 연도를 입력하세요. 검증은 각 연도의 직전 1년입니다.")

    try:
        if args.start is not None:
            compare_portfolio(args)
            return

        final_cutoff = args.through
        timezone = final_cutoff.tzinfo
        winners = []
        for year in sorted(set(args.years)):
            args.start = datetime(year, 1, 1, tzinfo=timezone)
            args.validation_from = args.start.replace(year=year - 1)
            year_end = args.start.replace(year=year + 1) - timedelta(microseconds=1)
            args.through = min(final_cutoff, year_end)
            print(f"\n=== {year}년 평가 | {year - 1}년 검증 ===")
            results = compare_portfolio(args)
            held = {name: results[name] for name in (
                STRATEGIES[0], "RSI 보유 출발", "Ridge 보유 출발", "GBRT 보유 출발",
            ) if name in results}
            highest_return = max(value[0] for value in held.values())
            lowest_drawdown = min(value[1] for value in held.values())
            return_leaders = ", ".join(
                name for name, value in held.items() if isclose(value[0], highest_return)
            )
            drawdown_leaders = ", ".join(
                name for name, value in held.items() if isclose(value[1], lowest_drawdown)
            )
            winners.append((year, return_leaders, drawdown_leaders))

        print("\n보유 출발 전략 | 연도별 비교")
        for year, return_leaders, drawdown_leaders in winners:
            print(f"{year} | 최고 수익률 {return_leaders} | 최소 낙폭 {drawdown_leaders}")
    except (OSError, ValueError, csv.Error) as error:
        parser.exit(1, f"포트폴리오 백테스트 실패 | {error}\n")


if __name__ == "__main__":
    main()
