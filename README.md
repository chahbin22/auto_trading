# 토스증권 일봉 수집기

IONQ, RXRX, BBAI의 일봉을 조회하고 CSV에 저장합니다. 저장한 CSV로 SMA(5/10/20)를 다시 계산할 수 있습니다.

## 실행

기존 프로젝트는 보관하고 새 폴더에서 실행하세요. Python 3.10 이상이 필요합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

기존 프로젝트의 .env를 이 폴더에 복사하세요. 처음 설정하는 경우 .env.example을 .env로 복사하고 토스증권 키를 채우세요. 토스증권에 현재 네트워크의 공인 IP가 등록되어 있어야 합니다.

```bash
python main.py collect IONQ
python main.py inspect IONQ
python main.py collect
python main.py collect IONQ --pages 5
```

collect는 토큰을 한 번 발급하고 종목당 기본 두 페이지를 조회합니다. 종목을 생략하면 세 종목을 처리합니다. inspect는 로컬 CSV만 읽습니다. 조회 오류나 빈 응답이 발생하면 해당 종목의 기존 CSV를 보존합니다.

CSV는 data/에 종목별로 저장되며, 성공한 새 조회 결과로 교체됩니다. 더 적은 페이지로 다시 수집하면 CSV 범위도 줄어듭니다. 시각은 토스 응답 그대로 보존하고 행은 오래된 순서로 정렬합니다.

최신 봉은 조회 시점에 따라 값이 달라질 수 있습니다. 확인한 기준 봉까지만 계산하려면 CSV에 표시된 시각을 아래 예시의 시각 대신 입력하세요. 지정 시각의 봉도 포함됩니다.

```bash
python main.py inspect IONQ --through '2026-11-09T14:00:00+09:00'
```

## 코드 구성

- toss_client.py에서 토큰과 캔들 API를 처리합니다.
- storage.py에서 일봉 CSV를 저장하고 읽습니다.
- indicators.py에서 SMA를 계산합니다.
- main.py에서 수집과 조회 명령을 실행합니다.

## 백테스트

수집한 일봉 CSV로 SMA(5/20), 20거래일 모멘텀, 매수 후 보유를 비교합니다.
SMA와 모멘텀은 종가로 신호를 계산하고 다음 봉 시가에 가상 체결합니다.
기본 초기 자금은 10,000 USD, 편도 비용은 0.10%입니다.

```bash
python3 backtest.py IONQ --through '2026-11-09T14:00:00+09:00' --export-trades
```

`--through`에는 사용할 마지막 확정 봉의 시각을 입력합니다.
`--from`으로 평가 시작 시각을 지정할 수 있습니다.
`--export-trades`를 사용하면 `IONQ_sma_trades.csv`에 SMA 거래별 체결 시각과 비용 차감 손익을 저장합니다.