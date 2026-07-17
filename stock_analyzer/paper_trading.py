from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Recommendation, StockQuote


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
) -> Path:
    paper_dir = data_dir / "paper_trading"
    paper_dir.mkdir(parents=True, exist_ok=True)
    state_path = paper_dir / "state.json"
    history_path = paper_dir / "account_history.json"
    operations_path = paper_dir / "operations.jsonl"
    strategy_path = paper_dir / "strategy.json"
    strategy_history_path = paper_dir / "strategy_history.json"
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

    save_state(state_path, state)
    save_strategy(strategy_path, run_date, strategy)
    append_strategy_history(strategy_history_path, run_date, strategy)
    append_history(history_path, run_date, state, market_note, strategy)
    append_operations(operations_path, run_date, actions, strategy)
    report_path = paper_dir / f"{run_date}.md"
    write_daily_report(report_path, run_date, state, picks, actions, market_note, strategy)
    return report_path


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


def write_daily_report(
    path: Path,
    run_date: str,
    state: PaperState,
    picks: list[Recommendation],
    actions: list[dict[str, Any]],
    market_note: str,
    strategy: StrategyProfile,
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
