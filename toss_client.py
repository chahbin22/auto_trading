import os

import requests
from dotenv import load_dotenv


BASE_URL = "https://openapi.tossinvest.com"


def issue_access_token():
    load_dotenv()
    client_id = os.getenv("TOSS_CLIENT_ID")
    client_secret = os.getenv("TOSS_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ValueError(".env에 TOSS_CLIENT_ID와 TOSS_CLIENT_SECRET을 설정하세요.")

    response = requests.post(
        f"{BASE_URL}/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=10,
    )
    if response.status_code == 403:
        raise ValueError("토큰 발급 HTTP 403. 토스증권의 허용 IP 설정을 확인하세요.")
    response.raise_for_status()
    return response.json()["access_token"]


def get_daily_candle_page(symbol, access_token, before=None):
    params = {"symbol": symbol, "interval": "1d", "count": 200}
    if before is not None:
        params["before"] = before
    response = requests.get(
        f"{BASE_URL}/api/v1/candles",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["result"]


def get_daily_candles(symbol, access_token, max_pages):
    candles_by_timestamp = {}
    requested_cursors = set()
    before = None

    for _ in range(max_pages):
        requested_cursors.add(before)
        page = get_daily_candle_page(symbol, access_token, before)
        candles = page["candles"]
        for candle in candles:
            candles_by_timestamp.setdefault(candle["timestamp"], candle)

        before = page.get("nextBefore")
        if not candles or not before:
            break
        if before in requested_cursors:
            raise ValueError("토스 API가 같은 페이지 위치를 반복해서 반환했습니다.")

    return list(candles_by_timestamp.values())
