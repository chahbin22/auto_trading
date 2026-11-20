#!/bin/bash
set -u
cd "$(dirname "$0")/.." || exit 1
log=".paper-rsi-refresh.log"
status=".paper-rsi-status"
if [[ -x .venv/bin/python3 ]]; then
  python=.venv/bin/python3
else
  python=venv/bin/python3
fi
printf '\n[%s]\n' "$(date '+%F %T %Z')" >> "$log"
if "$python" paper_rsi_notify.py >> "$log" 2>&1; then
  result="갱신 성공"
  code=0
else
  result="갱신 실패 · 로그 확인"
  code=1
fi
printf '%s  %s\n' "$result" "$(date '+%F %T %Z')" > "$status.tmp"
mv -f "$status.tmp" "$status"
exit "$code"
