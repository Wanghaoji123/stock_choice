#!/usr/bin/env bash
set -uo pipefail

project_dir="/home/wanghaoji/stock_choice"
log_dir="$project_dir/data/logs"
alert_dir="$project_dir/data/alerts"
run_date="$(date +%F)"
mkdir -p "$log_dir" "$alert_dir"

exec 9>"$project_dir/data/daily-run.lock"
if ! flock -n 9; then
  exit 0
fi

cd "$project_dir" || exit 1
/usr/bin/python3 -u main.py --once --full-scan --news-candidates 120 --news-per-stock 5 \
  --paper-trade --paper-capital 20000 >>"$log_dir/$run_date.log" 2>&1
status=$?

report="$project_dir/data/paper_trading/$run_date.md"
if [[ $status -eq 0 ]] && grep -q "模拟盘未运行：K线最后交易日不是今天" "$log_dir/$run_date.log"; then
  exit 0
fi
if [[ $status -ne 0 || ! -s "$report" ]]; then
  printf 'run_date=%s\nexit_code=%s\nreport=%s\n' "$run_date" "$status" "$report" \
    >"$alert_dir/${run_date}_failed.txt"
  if command -v notify-send >/dev/null 2>&1; then
    notify-send "stock_choice 运行失败" "请检查 $log_dir/$run_date.log"
  fi
  exit 1
fi

rm -f "$alert_dir/${run_date}_failed.txt"
if command -v notify-send >/dev/null 2>&1; then
  notify-send "stock_choice 已完成" "已生成 $run_date 模拟盘报告"
fi
