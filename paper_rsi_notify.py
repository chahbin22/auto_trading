import argparse
import json
import os
import subprocess
import sys
from fcntl import LOCK_EX, flock
from pathlib import Path


ROOT = Path(__file__).resolve().parent

NOTIFICATION_SCRIPT = """
on run argv
    display notification (item 2 of argv) with title (item 1 of argv)
end run
"""


def notify(title, message):
    result = subprocess.run(
        [
            "/usr/bin/osascript",
            "-e",
            NOTIFICATION_SCRIPT,
            title,
            message,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print("맥 알림 요청 실패 | 시스템 알림 설정을 확인하세요.")

def read_fills():
    ledger_path = ROOT / "paper_rsi_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))

    fills = {}

    for session in ledger["sessions"]:
        for symbol, row in session["symbols"].items():
            fill = row.get("fill")

            if fill:
                key = (
                    symbol,
                    fill["fill_at"],
                    fill["side"],
                )
                fills[key] = fill

    return fills

def refresh_and_notify():
    before = read_fills()

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "paper_rsi_refresh.py"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    print(result.stdout, end="", flush=True)
    print(result.stderr, end="", file=sys.stderr, flush=True)

    if result.returncode != 0:
        notify(
            "Paper RSI · 갱신 실패",
            "일봉 또는 모의 장부를 갱신하지 못했습니다. 실행 로그를 확인하세요.",
        )
        return result.returncode

    after = read_fills()
    new_fills = []

    for key in after:
        if key not in before:
            symbol, filled_at, side = key
            action = "가상 매수" if side == "BUY" else "가상 매도"
            new_fills.append(f"{symbol} {action}")

    if new_fills:
        message = f"{len(new_fills)}건 · " + ", ".join(new_fills[:4])
        notify("Paper RSI · 가상 체결", message)

    return 0

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    if args.test:
        notify(
            "Paper RSI · 알림 테스트",
            "갱신 실패와 가상 체결 발생 시 이곳에 표시됩니다.",
        )
        return 0

    os.chdir(ROOT)

    lock_path = ROOT / ".paper-rsi-notify.lock"

    with lock_path.open("a") as lock:
        flock(lock, LOCK_EX)

        try:
            return refresh_and_notify()
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f"알림 갱신 실패 | {error}", file=sys.stderr)

            notify(
                "Paper RSI · 갱신 실패",
                "갱신 작업을 실행하지 못했습니다. 실행 로그를 확인하세요.",
            )
            return 1


if __name__ == "__main__":
    sys.exit(main())