from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import KLine, Recommendation, StockQuote


LOT_SIZE = 100
MAX_POSITIONS = 2
MAX_POSITION_VALUE = 10_000.0
MIN_BUY_SCORE = 55.0


@dataclass
class Position:
    code: str
    name: str
    shares: int
    avg_cost: float
    last_price: float

    @property
    def market_value(self) -> float:
        return self.shares * self.last_price

    @property
    def cost_value(self) -> float:
        return self.shares * self.avg_cost

    @property
    def pnl(self) -> float:
        return self.market_value - self.cost_value

    @property
    def pnl_pct(self) -> float:
        return self.pnl / self.cost_value * 100 if self.cost_value else 0.0


@dataclass
class PaperState:
    initial_capital: float
    cash: float
    positions: dict[str, Position]

    @property
    def position_value(self) -> float:
        return sum(position.market_value for position in self.positions.values())

    @property
    def total_value(self) -> float:
        return self.cash + self.position_value

    @property
    def total_pnl(self) -> float:
        return self.total_value - self.initial_capital

    @property
    def total_pnl_pct(self) -> float:
        return self.total_pnl / self.initial_capital * 100 if self.initial_capital else 0.0


@dataclass(frozen=True)
class StrategyProfile:
    mode: str
    buy_score: float
    max_position_value: float
    max_positions: int
    stop_loss_pct: float
    reason: str


def run_paper_trading(
    data_dir: Path,
    run_date: str,
    picks: list[Recommendation],
    quotes: list[StockQuote],
    initial_capital: float,
    klines_by_code: dict[str, list[KLine]] | None = None,
) -> Path:
    paper_dir = data_dir / "paper_trading"
    paper_dir.mkdir(parents=True, exist_ok=True)
    state_path = paper_dir / "state.json"
    history_path = paper_dir / "account_history.json"
    operations_path = paper_dir / "operations.jsonl"
    strategy_path = paper_dir / "strategy.json"
    strategy_history_path = paper_dir / "strategy_history.json"
    timing_plan_path = paper_dir / f"timing_plan_{run_date}.json"
    timing_plan_history_path = paper_dir / "timing_plan_history.json"
    state = load_state(state_path, initial_capital)
    history = load_history(history_path)
    strategy = choose_strategy(history)
    quote_by_code = {quote.code: quote for quote in quotes}
    pick_by_code = {pick.code: pick for pick in picks}
    update_position_prices(state, quote_by_code)

    market_note, market_stressed = assess_market(quotes)
    actions: list[dict[str, Any]] = []
    actions.extend(apply_sell_rules(state, pick_by_code, strategy))
    if not market_stressed:
        actions.extend(apply_buy_rules(state, picks, strategy))
    else:
        actions.append(
            {
                "action": "WATCH",
                "code": "",
                "name": "",
                "shares": 0,
                "price": None,
                "amount": 0.0,
                "reason": "市场处于极端弱势，模拟盘不新开仓，等待次日确认承接。",
            }
        )

    timing_plans = build_timing_plans(state, picks, strategy, klines_by_code or {})
    save_state(state_path, state)
    save_strategy(strategy_path, run_date, strategy)
    append_strategy_history(strategy_history_path, run_date, strategy)
    save_timing_plan(timing_plan_path, run_date, timing_plans)
    append_timing_plan_history(timing_plan_history_path, run_date, timing_plans)
    append_history(history_path, run_date, state, market_note, strategy)
    append_operations(operations_path, run_date, actions, strategy)
    report_path = paper_dir / f"{run_date}.md"
    write_daily_report(
        report_path,
        run_date,
        state,
        picks,
        actions,
        market_note,
        strategy,
        timing_plans,
    )
    return report_path


def generate_monthly_summary(data_dir: Path, month: str) -> Path:
    paper_dir = data_dir / "paper_trading"
    paper_dir.mkdir(parents=True, exist_ok=True)
    history = [item for item in load_history(paper_dir / "account_history.json") if in_month(item, month)]
    operations = [item for item in load_operations(paper_dir / "operations.jsonl") if in_month(item, month)]
    strategies = [item for item in load_history(paper_dir / "strategy_history.json") if in_month(item, month)]

    summary_path = paper_dir / f"summary_{month}.md"
    lines = build_monthly_summary_lines(month, history, operations, strategies)
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    return summary_path


def load_state(path: Path, initial_capital: float) -> PaperState:
    if not path.exists():
        return PaperState(initial_capital=initial_capital, cash=initial_capital, positions={})
    with path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    positions = {
        code: Position(
            code=code,
            name=str(item["name"]),
            shares=int(item["shares"]),
            avg_cost=float(item["avg_cost"]),
            last_price=float(item.get("last_price") or item["avg_cost"]),
        )
        for code, item in (payload.get("positions") or {}).items()
    }
    return PaperState(
        initial_capital=float(payload.get("initial_capital") or initial_capital),
        cash=float(payload.get("cash") or 0.0),
        positions=positions,
    )


def save_state(path: Path, state: PaperState) -> None:
    payload = {
        "initial_capital": round(state.initial_capital, 2),
        "cash": round(state.cash, 2),
        "positions": {
            code: {
                "name": position.name,
                "shares": position.shares,
                "avg_cost": round(position.avg_cost, 4),
                "last_price": round(position.last_price, 4),
            }
            for code, position in state.positions.items()
        },
    }
    with path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


def load_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    return payload if isinstance(payload, list) else []


def load_operations(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def in_month(item: dict[str, Any], month: str) -> bool:
    return str(item.get("run_date") or "").startswith(f"{month}-")


def build_monthly_summary_lines(
    month: str,
    history: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    strategies: list[dict[str, Any]],
) -> list[str]:
    lines = [
        f"# 模拟投资月度复盘 {month}",
        "",
        "说明：这是纸面模拟复盘，不构成真实投资建议。",
        "",
    ]
    if not history:
        lines.extend(
            [
                "## 概览",
                "",
                "- 本月没有账户历史记录。请先运行每日模拟盘命令。",
                "",
            ]
        )
        return lines

    history.sort(key=lambda item: str(item.get("run_date", "")))
    start_value = infer_start_value(history)
    end_value = float(history[-1].get("total_value") or 0.0)
    total_pnl = end_value - start_value
    total_pnl_pct = total_pnl / start_value * 100 if start_value else 0.0
    daily_pcts = [float(item.get("daily_pnl_pct") or 0.0) for item in history]
    win_days = sum(1 for value in daily_pcts if value > 0)
    loss_days = sum(1 for value in daily_pcts if value < 0)
    flat_days = len(daily_pcts) - win_days - loss_days
    max_drawdown = calc_max_drawdown(history, start_value)
    action_counts = Counter(str(item.get("action") or "UNKNOWN") for item in operations)
    strategy_counts = Counter(str(item.get("mode") or item.get("strategy_mode") or "unknown") for item in strategies)
    if not strategy_counts:
        strategy_counts = Counter(str(item.get("strategy_mode") or "unknown") for item in history)

    lines.extend(
        [
            "## 概览",
            "",
            f"- 统计交易日：{len(history)} 天",
            f"- 期初资产：{start_value:.2f}",
            f"- 期末资产：{end_value:.2f}",
            f"- 本月收益：{total_pnl:+.2f} ({total_pnl_pct:+.2f}%)",
            f"- 最大回撤：{max_drawdown:.2f}%",
            f"- 盈利天数：{win_days}，亏损天数：{loss_days}，持平天数：{flat_days}",
            "",
            "## 操作统计",
            "",
            f"- 买入次数：{action_counts.get('BUY', 0)}",
            f"- 卖出次数：{action_counts.get('SELL', 0)}",
            f"- 跳过次数：{action_counts.get('SKIP', 0)}",
            f"- 观察次数：{action_counts.get('WATCH', 0)}",
            "",
            "## 策略模式",
            "",
        ]
    )
    for mode, count in strategy_counts.most_common():
        lines.append(f"- {mode}：{count} 天")
    lines.extend(["", "## 每日资产", ""])
    lines.append("| 日期 | 总资产 | 当日收益 | 累计收益 | 持仓数 | 策略 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | --- |")
    for item in history:
        lines.append(
            f"| {item.get('run_date')} | {float(item.get('total_value') or 0.0):.2f} | "
            f"{float(item.get('daily_pnl') or 0.0):+.2f} ({float(item.get('daily_pnl_pct') or 0.0):+.2f}%) | "
            f"{float(item.get('total_pnl') or 0.0):+.2f} ({float(item.get('total_pnl_pct') or 0.0):+.2f}%) | "
            f"{int(item.get('position_count') or 0)} | {item.get('strategy_mode') or ''} |"
        )

    lines.extend(["", "## 买卖流水", ""])
    trade_rows = [item for item in operations if item.get("action") in {"BUY", "SELL"}]
    if trade_rows:
        lines.append("| 日期 | 操作 | 代码 | 名称 | 股数 | 价格 | 金额 | 原因 |")
        lines.append("| --- | --- | --- | --- | ---: | ---: | ---: | --- |")
        for item in trade_rows:
            lines.append(
                f"| {item.get('run_date')} | {item.get('action')} | {item.get('code')} | "
                f"{item.get('name')} | {int(item.get('shares') or 0)} | "
                f"{format_optional_float(item.get('price'))} | {float(item.get('amount') or 0.0):.2f} | "
                f"{item.get('reason') or ''} |"
            )
    else:
        lines.append("- 本月没有实际模拟买入或卖出。")

    lines.extend(["", "## 策略调整历史", ""])
    if strategies:
        lines.append("| 日期 | 模式 | 开仓分数 | 单票上限 | 最大持仓 | 止损线 | 原因 |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | --- |")
        for item in sorted(strategies, key=lambda row: str(row.get("run_date", ""))):
            lines.append(
                f"| {item.get('run_date')} | {item.get('mode')} | {float(item.get('buy_score') or 0.0):.0f} | "
                f"{float(item.get('max_position_value') or 0.0):.2f} | {int(item.get('max_positions') or 0)} | "
                f"{float(item.get('stop_loss_pct') or 0.0):.0f}% | {item.get('reason') or ''} |"
            )
    else:
        lines.append("- 本月没有策略历史记录。")

    lines.append("")
    lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return lines


def infer_start_value(history: list[dict[str, Any]]) -> float:
    first = history[0]
    total_value = float(first.get("total_value") or 0.0)
    daily_pnl = float(first.get("daily_pnl") or 0.0)
    start_value = total_value - daily_pnl
    return start_value if start_value > 0 else total_value


def calc_max_drawdown(history: list[dict[str, Any]], start_value: float) -> float:
    peak = start_value
    max_drawdown = 0.0
    for item in history:
        value = float(item.get("total_value") or 0.0)
        peak = max(peak, value)
        if peak <= 0:
            continue
        drawdown = (peak - value) / peak * 100
        max_drawdown = max(max_drawdown, drawdown)
    return max_drawdown


def format_optional_float(value: object) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return ""


def save_strategy(path: Path, run_date: str, strategy: StrategyProfile) -> None:
    payload = {
        "run_date": run_date,
        "mode": strategy.mode,
        "buy_score": strategy.buy_score,
        "max_position_value": strategy.max_position_value,
        "max_positions": strategy.max_positions,
        "stop_loss_pct": strategy.stop_loss_pct,
        "reason": strategy.reason,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    with path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


def append_strategy_history(path: Path, run_date: str, strategy: StrategyProfile) -> None:
    history = load_history(path)
    history = [item for item in history if item.get("run_date") != run_date]
    history.append(
        {
            "run_date": run_date,
            "mode": strategy.mode,
            "buy_score": strategy.buy_score,
            "max_position_value": strategy.max_position_value,
            "max_positions": strategy.max_positions,
            "stop_loss_pct": strategy.stop_loss_pct,
            "reason": strategy.reason,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    history.sort(key=lambda item: str(item.get("run_date", "")))
    with path.open("w", encoding="utf-8") as fp:
        json.dump(history, fp, ensure_ascii=False, indent=2)


def save_timing_plan(path: Path, run_date: str, timing_plans: list[dict[str, Any]]) -> None:
    payload = {
        "run_date": run_date,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "plans": timing_plans,
    }
    with path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


def append_timing_plan_history(path: Path, run_date: str, timing_plans: list[dict[str, Any]]) -> None:
    history = load_history(path)
    history = [item for item in history if item.get("run_date") != run_date]
    history.append(
        {
            "run_date": run_date,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "plans": timing_plans,
        }
    )
    history.sort(key=lambda item: str(item.get("run_date", "")))
    with path.open("w", encoding="utf-8") as fp:
        json.dump(history, fp, ensure_ascii=False, indent=2)


def append_history(
    path: Path,
    run_date: str,
    state: PaperState,
    market_note: str,
    strategy: StrategyProfile,
) -> None:
    history = load_history(path)
    previous_value = history[-1]["total_value"] if history else state.initial_capital
    daily_pnl = state.total_value - float(previous_value)
    daily_pnl_pct = daily_pnl / float(previous_value) * 100 if previous_value else 0.0
    history = [item for item in history if item.get("run_date") != run_date]
    history.append(
        {
            "run_date": run_date,
            "cash": round(state.cash, 2),
            "position_value": round(state.position_value, 2),
            "total_value": round(state.total_value, 2),
            "daily_pnl": round(daily_pnl, 2),
            "daily_pnl_pct": round(daily_pnl_pct, 4),
            "total_pnl": round(state.total_pnl, 2),
            "total_pnl_pct": round(state.total_pnl_pct, 4),
            "position_count": len(state.positions),
            "strategy_mode": strategy.mode,
            "market_note": market_note,
        }
    )
    history.sort(key=lambda item: str(item.get("run_date", "")))
    with path.open("w", encoding="utf-8") as fp:
        json.dump(history, fp, ensure_ascii=False, indent=2)


def append_operations(
    path: Path,
    run_date: str,
    actions: list[dict[str, Any]],
    strategy: StrategyProfile,
) -> None:
    with path.open("a", encoding="utf-8") as fp:
        for action in actions:
            record = dict(action)
            record["run_date"] = run_date
            record["strategy_mode"] = strategy.mode
            record["created_at"] = datetime.now().isoformat(timespec="seconds")
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")


def choose_strategy(history: list[dict[str, Any]]) -> StrategyProfile:
    if len(history) < 3:
        return StrategyProfile(
            mode="normal",
            buy_score=MIN_BUY_SCORE,
            max_position_value=MAX_POSITION_VALUE,
            max_positions=MAX_POSITIONS,
            stop_loss_pct=-8.0,
            reason="历史样本少于3天，先使用默认保守参数。",
        )
    recent = history[-5:]
    daily_pcts = [float(item.get("daily_pnl_pct") or 0.0) for item in recent]
    loss_days = sum(1 for value in daily_pcts if value < 0)
    recent_return = (
        (float(recent[-1]["total_value"]) - float(recent[0]["total_value"]))
        / float(recent[0]["total_value"])
        * 100
        if recent and float(recent[0].get("total_value") or 0.0)
        else 0.0
    )
    if loss_days >= 3 or recent_return <= -3.0:
        return StrategyProfile(
            mode="defensive",
            buy_score=65.0,
            max_position_value=6_000.0,
            max_positions=1,
            stop_loss_pct=-6.0,
            reason=f"近{len(recent)}次记录有 {loss_days} 天亏损，区间收益 {recent_return:+.2f}%，转为防守。",
        )
    win_days = sum(1 for value in daily_pcts if value > 0)
    if len(recent) >= 5 and win_days >= 4 and recent_return >= 2.0:
        return StrategyProfile(
            mode="positive",
            buy_score=52.0,
            max_position_value=10_000.0,
            max_positions=2,
            stop_loss_pct=-8.0,
            reason=f"近5次记录有 {win_days} 天盈利，区间收益 {recent_return:+.2f}%，允许正常偏积极。",
        )
    return StrategyProfile(
        mode="normal",
        buy_score=MIN_BUY_SCORE,
        max_position_value=MAX_POSITION_VALUE,
        max_positions=MAX_POSITIONS,
        stop_loss_pct=-8.0,
        reason=f"近{len(recent)}次记录区间收益 {recent_return:+.2f}%，维持默认策略。",
    )


def update_position_prices(state: PaperState, quote_by_code: dict[str, StockQuote]) -> None:
    for position in state.positions.values():
        quote = quote_by_code.get(position.code)
        if quote and quote.price:
            position.last_price = quote.price


def assess_market(quotes: list[StockQuote]) -> tuple[str, bool]:
    pct_values = [quote.pct_chg for quote in quotes if quote.pct_chg is not None]
    if not pct_values:
        return "市场涨跌数据不足，按普通风险处理。", False
    avg_pct = sum(pct_values) / len(pct_values)
    down_ratio = sum(1 for value in pct_values if value < 0) / len(pct_values)
    limit_down_like = sum(1 for value in pct_values if value <= -9.5)
    stressed = avg_pct <= -3.0 or down_ratio >= 0.72 or limit_down_like >= 20
    note = (
        f"样本平均涨跌幅 {avg_pct:+.2f}%，下跌占比 {down_ratio:.1%}，"
        f"接近跌停样本 {limit_down_like} 只。"
    )
    if stressed:
        note += " 判定为弱势/极端风险日。"
    else:
        note += " 未触发极端风险过滤。"
    return note, stressed


def apply_sell_rules(
    state: PaperState,
    pick_by_code: dict[str, Recommendation],
    strategy: StrategyProfile,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for code, position in list(state.positions.items()):
        pick = pick_by_code.get(code)
        day_pct = pick.pct_chg if pick else None
        risk_penalty = pick.risk_penalty if pick else 0.0
        should_sell = False
        reason = ""
        if position.pnl_pct <= strategy.stop_loss_pct:
            should_sell = True
            reason = f"触发 {strategy.stop_loss_pct:.0f}% 模拟止损，当前浮亏 {position.pnl_pct:.2f}%。"
        elif day_pct is not None and day_pct <= -7.0:
            should_sell = True
            reason = f"当日跌幅 {day_pct:.2f}%，先退出等待重新企稳。"
        elif risk_penalty >= 18.0:
            should_sell = True
            reason = f"风险扣分 {risk_penalty:.1f} 较高，降低事件风险暴露。"
        elif position.pnl_pct >= 15.0 and day_pct is not None and day_pct < 0:
            should_sell = True
            reason = f"浮盈 {position.pnl_pct:.2f}% 后转弱，模拟止盈。"
        if not should_sell:
            continue
        amount = position.shares * position.last_price
        state.cash += amount
        del state.positions[code]
        actions.append(
            {
                "action": "SELL",
                "code": code,
                "name": position.name,
                "shares": position.shares,
                "price": position.last_price,
                "amount": amount,
                "reason": reason,
            }
        )
    return actions


def apply_buy_rules(
    state: PaperState,
    picks: list[Recommendation],
    strategy: StrategyProfile,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for pick in picks:
        if len(state.positions) >= strategy.max_positions:
            break
        if pick.code in state.positions or pick.price is None:
            continue
        valid, reason = buy_filter_reason(pick, strategy)
        if not valid:
            actions.append(
                {
                    "action": "SKIP",
                    "code": pick.code,
                    "name": pick.name,
                    "shares": 0,
                    "price": pick.price,
                    "amount": 0.0,
                    "reason": reason,
                }
            )
            continue
        budget = min(strategy.max_position_value, state.cash * 0.5)
        shares = int(budget // (pick.price * LOT_SIZE)) * LOT_SIZE
        if shares < LOT_SIZE:
            actions.append(
                {
                    "action": "SKIP",
                    "code": pick.code,
                    "name": pick.name,
                    "shares": 0,
                    "price": pick.price,
                    "amount": 0.0,
                    "reason": "现金不足以按 A 股一手 100 股开仓。",
                }
            )
            continue
        amount = shares * pick.price
        state.cash -= amount
        state.positions[pick.code] = Position(
            code=pick.code,
            name=pick.name,
            shares=shares,
            avg_cost=pick.price,
            last_price=pick.price,
        )
        actions.append(
            {
                "action": "BUY",
                "code": pick.code,
                "name": pick.name,
                "shares": shares,
                "price": pick.price,
                "amount": amount,
                "reason": f"分数、趋势、风险过滤均通过，按 {strategy.mode} 模式模拟买入。",
            }
        )
    if not actions:
        actions.append(
            {
                "action": "WATCH",
                "code": "",
                "name": "",
                "shares": 0,
                "price": None,
                "amount": 0.0,
                "reason": "没有候选股满足开仓条件，保持现金。",
            }
        )
    return actions


def buy_filter_reason(pick: Recommendation, strategy: StrategyProfile) -> tuple[bool, str]:
    if pick.score < strategy.buy_score:
        return False, f"总分 {pick.score:.2f} 低于当前开仓阈值 {strategy.buy_score:.0f}。"
    if pick.trend_score < 50.0:
        return False, f"趋势分 {pick.trend_score:.1f} 偏弱。"
    if pick.risk_penalty > 12.0:
        return False, f"风险扣分 {pick.risk_penalty:.1f} 偏高。"
    if pick.pct_chg is not None and pick.pct_chg <= -3.0:
        return False, f"当日跌幅 {pick.pct_chg:.2f}%，不接下跌中的刀。"
    if pick.pct_chg is not None and pick.pct_chg >= 7.0:
        return False, f"当日涨幅 {pick.pct_chg:.2f}%，不追过热标的。"
    return True, "通过开仓过滤。"


def moving_average(klines: list[KLine], days: int) -> float | None:
    if len(klines) < days:
        return None
    return sum(item.close for item in klines[-days:]) / days


def recent_range(klines: list[KLine], days: int) -> tuple[float | None, float | None]:
    if not klines:
        return None, None
    rows = klines[-days:]
    return min(item.low for item in rows), max(item.high for item in rows)


def average_amplitude(klines: list[KLine], days: int = 10) -> float:
    rows = klines[-days:] if klines else []
    values: list[float] = []
    for item in rows:
        if item.amplitude is not None:
            values.append(item.amplitude)
        elif item.close:
            values.append((item.high - item.low) / item.close * 100)
    return sum(values) / len(values) if values else 3.0


def build_buy_timing_plan(
    pick: Recommendation,
    klines_by_code: dict[str, list[KLine]],
    strategy: StrategyProfile,
) -> dict[str, Any]:
    price = pick.price
    if price is None:
        return {
            "code": pick.code,
            "name": pick.name,
            "decision": "WAIT",
            "reason": "没有最新价格，不能计算次日价位计划。",
        }
    valid, filter_reason = buy_filter_reason(pick, strategy)
    klines = klines_by_code.get(pick.code) or []
    ma5 = moving_average(klines, 5)
    ma10 = moving_average(klines, 10)
    ma20 = moving_average(klines, 20)
    low20, high20 = recent_range(klines, 20)
    amplitude = average_amplitude(klines)
    pullback_pct = min(3.0, max(1.0, amplitude * 0.45))
    buy_low = price * (1 - pullback_pct / 100)
    support_candidates = [value for value in (ma5, ma10, ma20, low20) if value is not None and value < price]
    if support_candidates:
        buy_low = max(buy_low, max(support_candidates) * 0.995)
    buy_high = price * 1.01
    confirm = max(price * 1.015, (ma5 or price) * 1.005)
    stop = min(price * 0.94, (ma20 or price) * 0.985)
    if low20 is not None:
        stop = min(stop, low20 * 0.985)
    target1 = price * 1.04
    target2 = price * 1.08
    if high20 is not None and high20 > price:
        target1 = min(target1, high20)
        target2 = max(target1, high20 * 1.03)
    decision = "PLAN_BUY" if valid else "WAIT"
    reason_parts = [
        filter_reason,
        f"近10日平均振幅约 {amplitude:.1f}%，低吸区按回撤 {pullback_pct:.1f}% 估算。",
    ]
    if ma5 and ma10 and ma20:
        reason_parts.append(f"均线参考：MA5 {ma5:.2f}，MA10 {ma10:.2f}，MA20 {ma20:.2f}。")
    if low20 and high20:
        reason_parts.append(f"近20日区间 {low20:.2f}-{high20:.2f}。")
    return {
        "code": pick.code,
        "name": pick.name,
        "decision": decision,
        "buy_low": buy_low,
        "buy_high": buy_high,
        "confirm": confirm,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "reason": " ".join(reason_parts),
    }


def build_position_timing_plan(
    position: Position,
    klines_by_code: dict[str, list[KLine]],
    strategy: StrategyProfile,
) -> dict[str, Any]:
    klines = klines_by_code.get(position.code) or []
    ma5 = moving_average(klines, 5)
    ma10 = moving_average(klines, 10)
    ma20 = moving_average(klines, 20)
    low20, high20 = recent_range(klines, 20)
    stop_by_cost = position.avg_cost * (1 + strategy.stop_loss_pct / 100)
    stop_by_trend = (ma20 * 0.985) if ma20 else position.last_price * 0.94
    stop = max(stop_by_cost, stop_by_trend)
    reduce_price = position.last_price * 1.05
    if high20 and high20 > position.last_price:
        reduce_price = min(reduce_price, high20)
    target = max(position.avg_cost * 1.12, position.last_price * 1.08)
    reason_parts = [
        f"成本 {position.avg_cost:.2f}，当前浮动收益 {position.pnl_pct:+.2f}%。",
        f"止损同时参考成本止损和 MA20 趋势线，取 {stop:.2f}。",
    ]
    if ma5 and ma10 and ma20:
        reason_parts.append(f"均线参考：MA5 {ma5:.2f}，MA10 {ma10:.2f}，MA20 {ma20:.2f}。")
    if low20 and high20:
        reason_parts.append(f"近20日区间 {low20:.2f}-{high20:.2f}。")
    return {
        "code": position.code,
        "name": position.name,
        "decision": "HOLD_PLAN",
        "stop": stop,
        "reduce_price": reduce_price,
        "target": target,
        "reason": " ".join(reason_parts),
    }


def build_timing_plans(
    state: PaperState,
    picks: list[Recommendation],
    strategy: StrategyProfile,
    klines_by_code: dict[str, list[KLine]],
) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for position in state.positions.values():
        plan = build_position_timing_plan(position, klines_by_code, strategy)
        plan["plan_type"] = "position"
        plans.append(plan)
    for pick in picks:
        plan = build_buy_timing_plan(pick, klines_by_code, strategy)
        plan["plan_type"] = "candidate"
        plans.append(plan)
    return plans


def format_price(value: object) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "暂无"


def write_daily_report(
    path: Path,
    run_date: str,
    state: PaperState,
    picks: list[Recommendation],
    actions: list[dict[str, Any]],
    market_note: str,
    strategy: StrategyProfile,
    timing_plans: list[dict[str, Any]],
) -> None:
    lines = [
        f"# 模拟投资日报 {run_date}",
        "",
        "说明：这是纸面模拟，不构成真实投资建议。",
        "",
        "## 账户",
        "",
        f"- 初始本金：{state.initial_capital:.2f}",
        f"- 现金：{state.cash:.2f}",
        f"- 持仓市值：{state.position_value:.2f}",
        f"- 总资产：{state.total_value:.2f}",
        f"- 累计收益：{state.total_pnl:+.2f} ({state.total_pnl_pct:+.2f}%)",
        "",
        "## 市场判断",
        "",
        f"- {market_note}",
        "",
        "## 当前策略",
        "",
        f"- 模式：{strategy.mode}",
        f"- 开仓分数阈值：{strategy.buy_score:.0f}",
        f"- 单票上限：{strategy.max_position_value:.2f}",
        f"- 最大持仓数：{strategy.max_positions}",
        f"- 止损线：{strategy.stop_loss_pct:.0f}%",
        f"- 调整原因：{strategy.reason}",
        "",
        "## 今日操作",
        "",
    ]
    for action in actions:
        if action["action"] in {"BUY", "SELL"}:
            lines.append(
                "- {action} {code} {name} {shares} 股，价格 {price:.2f}，金额 {amount:.2f}。{reason}".format(
                    **action
                )
            )
        elif action["code"]:
            lines.append(f"- {action['action']} {action['code']} {action['name']}：{action['reason']}")
        else:
            lines.append(f"- {action['action']}：{action['reason']}")
    lines.extend(["", "## 次日价位计划", ""])
    lines.append("说明：以下价位基于日 K 推导，用于下一交易日盘中观察，不代表已经成交。")
    lines.append("")
    position_plans = [plan for plan in timing_plans if plan.get("plan_type") == "position"]
    candidate_plans = [plan for plan in timing_plans if plan.get("plan_type") == "candidate"]
    if position_plans:
        lines.append("持仓处理：")
        for plan in position_plans:
            lines.append(
                f"- {plan['code']} {plan['name']}：跌破 {format_price(plan['stop'])} 先减风险；"
                f"冲到 {format_price(plan['reduce_price'])} 附近可考虑减仓；"
                f"强势目标 {format_price(plan['target'])}。{plan['reason']}"
            )
        lines.append("")
    lines.append("候选观察：")
    if candidate_plans:
        for plan in candidate_plans:
            if plan["decision"] == "PLAN_BUY":
                lines.append(
                    f"- {plan['code']} {plan['name']}：低吸区 {format_price(plan['buy_low'])}-"
                    f"{format_price(plan['buy_high'])}；放量站上 {format_price(plan['confirm'])} 属于确认买点；"
                    f"止损 {format_price(plan['stop'])}；止盈参考 {format_price(plan['target1'])}/"
                    f"{format_price(plan['target2'])}。{plan['reason']}"
                )
            else:
                lines.append(f"- {plan['code']} {plan['name']}：暂不买入。{plan['reason']}")
    else:
        lines.append("- 没有候选股可制定价位计划。")
    lines.extend(["", "## 当前持仓", ""])
    if state.positions:
        lines.append("| 代码 | 名称 | 股数 | 成本 | 最新价 | 市值 | 浮动收益 |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
        for position in state.positions.values():
            lines.append(
                f"| {position.code} | {position.name} | {position.shares} | "
                f"{position.avg_cost:.2f} | {position.last_price:.2f} | "
                f"{position.market_value:.2f} | {position.pnl:+.2f} ({position.pnl_pct:+.2f}%) |"
            )
    else:
        lines.append("- 空仓")
    lines.extend(["", "## 今日候选", ""])
    if picks:
        for rank, pick in enumerate(picks, start=1):
            pct = f"{pick.pct_chg:+.2f}%" if pick.pct_chg is not None else "暂无"
            lines.append(
                f"- {rank}. {pick.code} {pick.name}：价格 {pick.price}，涨跌幅 {pct}，"
                f"总分 {pick.score:.2f}，趋势 {pick.trend_score:.1f}，风险扣分 {pick.risk_penalty:.1f}。"
            )
    else:
        lines.append("- 未筛选出候选股票。")
    lines.append("")
    lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    path.write_text("\n".join(lines), encoding="utf-8")
