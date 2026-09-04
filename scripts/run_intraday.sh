#!/usr/bin/env bash
set -uo pipefail

project_dir="/home/wanghaoji/stock_choice"
log_dir="$project_dir/data/logs"
run_date="$(date +%F)"
session_time="${1:-09:35}"
case "$session_time" in
  09:35|10:30|14:50) ;;
  09:*|10:0*) session_time="09:35" ;;
  10:*|11:*|12:*|13:*) session_time="10:30" ;;
  *) session_time="14:50" ;;
esac
session_key="${session_time/:/}"
mkdir -p "$log_dir"

exec 9>"$project_dir/data/intraday-run.lock"
if ! flock -n 9; then
  exit 0
fi

cd "$project_dir" || exit 1
/usr/bin/python3 -u main.py --paper-execute-pending --session-time "$session_time" \
  --paper-capital 20000 >>"$log_dir/${run_date}_intraday_${session_key}.log" 2>&1
