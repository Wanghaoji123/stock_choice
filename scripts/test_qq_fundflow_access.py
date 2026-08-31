"""并发测试腾讯财经资金流公开接口，不写数据库。"""

from __future__ import annotations

import concurrent.futures
import argparse
import json
import sqlite3
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FIELDS = ("mainNetIn", "superFlow", "bigFlow", "normalFlow", "smallFlow")


def symbol(code: str) -> str:
    return ("sh" if code.startswith(("5", "6", "9")) else "sz") + code


def fetch(code: str) -> dict:
    url = (
        "https://proxy.finance.qq.com/cgi/cgi-bin/fundflow/hsfundtab"
        f"?code={symbol(code)}&type=todayFundFlow&klineNeedDay=1"
    )
    try:
        result = subprocess.run(
            [
                "curl", "-L", "--compressed", "--silent", "--show-error",
                "--max-time", "8", "-H", "Referer: https://gu.qq.com/", url,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        flow = json.loads(result.stdout).get("data", {}).get("todayFundFlow") or {}
        values = {key: int(flow[key]) for key in FIELDS}
        if abs(values["mainNetIn"] - values["superFlow"] - values["bigFlow"]) > 2:
            raise ValueError("主力净流入不等于超大单与大单之和")
        values["code"] = code
        return values
    except Exception as exc:
        return {"code": code, "error": str(exc)}


def recent_codes(limit: int = 30) -> list[str]:
    db = sqlite3.connect(ROOT / "data" / "stock_analysis.sqlite3")
    try:
        rows = db.execute(
            "SELECT code FROM quotes GROUP BY code ORDER BY MAX(fetched_at) DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [str(row[0]) for row in rows]
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    codes = recent_codes(max(1, args.limit))
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        rows = list(pool.map(fetch, codes))
    elapsed = time.monotonic() - started
    successes = [row for row in rows if "error" not in row]
    failures = [row for row in rows if "error" in row]
    print(f"腾讯资金流小样本：成功 {len(successes)}/{len(rows)}，耗时 {elapsed:.1f} 秒")
    if successes:
        print("样例：", successes[0])
    if failures:
        print("首个错误：", failures[0])
    if len(successes) / max(len(rows), 1) < 0.8:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
