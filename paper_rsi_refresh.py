"""Update the existing RSI paper ledger from Toss daily candles."""
import json
import os
import sys
import subprocess
from datetime import datetime, time
from fcntl import LOCK_EX, flock
from pathlib import Path
from zoneinfo import ZoneInfo

from paper_rsi import SYMBOLS
from storage import candle_time, read_candles, save_snapshot
from toss_client import get_daily_candles, issue_access_token

ROOT = Path(__file__).resolve().parent
LEDGER = ROOT / "paper_rsi_ledger.json"
NEW_YORK = ZoneInfo("America/New_York")


def confirmed(stamp, now):
    session_date = stamp.astimezone(NEW_YORK).date()
    return session_date < now.date() or (
        session_date == now.date() and now.time() >= time(17)
    )


def refresh():
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    checkpoint = datetime.fromisoformat(ledger["sessions"][-1]["bar_at"])
    now = datetime.now(NEW_YORK)
    if not confirmed(checkpoint, now):
        print("확정 대기 | 장부의 마지막 봉이 확정되면 다시 갱신합니다.")
        return
    token = issue_access_token()
    merged = {}
    for symbol in SYMBOLS:
        existing = {candle_time(row): row for row in read_candles(symbol)}
        fresh = {candle_time(row): row for row in get_daily_candles(symbol, token, 1)
                 if confirmed(candle_time(row), now)}
        if not fresh or max(fresh) < checkpoint:
            raise ValueError(f"{symbol} | 최신 확정 봉을 받지 못했습니다.")
        if checkpoint not in existing or min(fresh) > checkpoint:
            raise ValueError(f"{symbol} | 장부와 일봉 조회 범위가 이어지지 않습니다.")
        existing = {stamp: row for stamp, row in existing.items() if stamp <= max(fresh)}
        existing.update(fresh)
        merged[symbol] = existing

    new_stamps = [
        {stamp for stamp in rows if stamp > checkpoint}
        for rows in merged.values()
    ]
    if any(stamps != new_stamps[0] for stamps in new_stamps[1:]):
        raise ValueError("세 종목의 새 확정 일봉이 일치하지 않습니다.")

    for symbol, rows in merged.items():
        save_snapshot(symbol, rows.values())
    cutoff = max(new_stamps[0], default=checkpoint)
    # Keep the refresh lock held across the ledger subprocess.
    subprocess.run([sys.executable, str(ROOT / "paper_rsi.py"),
                    "--through", cutoff.isoformat()], cwd=ROOT, check=True)


if __name__ == "__main__":
    os.chdir(ROOT)
    try:
        with (ROOT / ".paper-rsi-refresh.lock").open("a") as lock:
            flock(lock, LOCK_EX)
            refresh()
    except Exception as error:
        sys.exit(f"자동 갱신 실패 | {error}")
