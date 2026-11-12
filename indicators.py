def calculate_sma(closing_prices, period):
    if period <= 0 or len(closing_prices) < period:
        raise ValueError(f"SMA({period}) 계산에 필요한 종가가 부족합니다.")
    # 종가는 오래된 봉부터 최신 봉 순서로 정렬되어 있다.
    return sum(closing_prices[-period:]) / period
