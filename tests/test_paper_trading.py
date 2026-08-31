from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from stock_analyzer.models import Recommendation, StockQuote
from stock_analyzer.config import Settings
from stock_analyzer.fetchers import EastMoneyClient
from stock_analyzer.paper_trading import (
    MarketProfile,
    PaperState,
    Position,
    StrategyProfile,
    apply_sell_rules,
    buy_filter_reason,
    create_pending_orders,
    execute_pending_orders,
    update_account_risk,
    update_signal_observations,
)


def quote(code: str = "600000", price: float = 10.2, open_price: float = 10.0,
          high: float = 10.4, low: float = 9.9) -> StockQuote:
    return StockQuote(
        code=code, name="测试", market="sh", price=price, pct_chg=2.0,
        volume=1_000_000, amount=100_000_000, turnover_rate=3.0,
        market_cap=10_000_000_000, fetched_at=datetime.now(),
        open_price=open_price, high_price=high, low_price=low, previous_close=10.0,
    )


def pick(score: float = 70.0) -> Recommendation:
    q = quote()
    return Recommendation(
        code=q.code, name=q.name, price=q.price, pct_chg=q.pct_chg, score=score,
        volume_score=70, amount_score=70, news_score=50, trend_score=70,
        liquidity_score=70, capital_cohesion_score=85, distribution_penalty=0,
        risk_penalty=0, reasons=("超大单与大单同向净流入，资金合力确认",),
        quote=q, latest_news=(),
    )


STRATEGY = StrategyProfile("normal", 65, 10_000, 2, -5, "test")
MARKET = MarketProfile("normal", 0.5, 0, 1, 2, "test")


class PaperTradingTests(unittest.TestCase):
    def test_quote_batch_fields_populate_capital_flow(self) -> None:
        client = EastMoneyClient(Settings())
        item = {
            "f12": "600000", "f14": "测试", "f2": 10, "f3": 1,
            "f5": 100, "f6": 1000, "f8": 2, "f15": 10.2, "f16": 9.8,
            "f17": 9.9, "f18": 9.9, "f20": 100000,
            "f62": 100, "f66": 70, "f72": 30, "f78": -20, "f84": -80,
        }
        parsed = client._quote_from_eastmoney_item(item, datetime.now())
        self.assertIsNotNone(parsed)
        self.assertEqual(client.bulk_capital_flows["600000"].extra_large_net, 70.0)
        self.assertEqual(client.bulk_capital_flows["600000"].large_net, 30.0)

    def test_daily_capital_flow_endpoint_is_parsed(self) -> None:
        client = EastMoneyClient(Settings())
        today = date.today().isoformat()
        client._get_json = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
            "data": {"klines": [f"{today},100,10,20,30,70,1,2,3,4,5"]}
        }
        flow = client.fetch_capital_flow("600000")
        self.assertIsNotNone(flow)
        assert flow is not None
        self.assertEqual(flow.large_net, 30.0)
        self.assertEqual(flow.extra_large_net, 70.0)
        self.assertEqual(client.capital_flow_stats["success"], 1)

    def test_stale_capital_flow_is_rejected(self) -> None:
        client = EastMoneyClient(Settings())
        stale = (date.today() - timedelta(days=1)).isoformat()
        client._get_json = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
            "data": {"klines": [f"{stale},100,10,20,30,70,1,2,3,4,5"]}
        }
        self.assertIsNone(client.fetch_capital_flow("600000"))
        self.assertEqual(client.capital_flow_stats["stale"], 1)

    def test_buy_score_is_a_real_filter(self) -> None:
        valid, reason = buy_filter_reason(pick(64), STRATEGY, [])
        self.assertFalse(valid)
        self.assertIn("低于当前开仓线", reason)

    def test_signal_is_filled_on_next_day_with_costs(self) -> None:
        state = PaperState(20_000, 20_000, {}, [], 20_000)
        actions = create_pending_orders(state, [pick()], STRATEGY, MARKET, {}, "2026-08-27")
        self.assertEqual(actions[0]["action"], "ORDER")
        self.assertFalse(state.positions)
        fills = execute_pending_orders(state, {"600000": quote()}, "2026-08-28")
        self.assertEqual(fills[0]["action"], "BUY")
        self.assertIn("600000", state.positions)
        amount_at_fill = state.positions["600000"].shares * fills[0]["price"]
        self.assertLess(state.cash + amount_at_fill, 20_000)

    def test_five_percent_stop_uses_intraday_low(self) -> None:
        position = Position("600000", "测试", 500, 10.0, 9.8, "2026-08-20", 10.2, 3)
        state = PaperState(20_000, 15_000, {"600000": position}, [], 20_000)
        stop_quote = quote(price=9.7, open_price=9.8, high=9.9, low=9.4)
        actions = apply_sell_rules(state, {}, STRATEGY, {"600000": stop_quote}, "2026-08-27")
        self.assertEqual(actions[0]["action"], "SELL")
        self.assertNotIn("600000", state.positions)
        self.assertLess(actions[0]["price"], 9.51)

    def test_account_halts_at_fifteen_percent_drawdown(self) -> None:
        state = PaperState(20_000, 16_900, {}, [], 20_000)
        update_account_risk(state)
        self.assertTrue(state.trading_halted)

    def test_signal_forward_returns_are_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signals.json"
            update_signal_observations(path, "2026-08-27", {"600000": quote()}, [pick()])
            update_signal_observations(path, "2026-08-28", {"600000": quote(price=10.5)}, [])
            content = path.read_text(encoding="utf-8")
            self.assertIn('"t1_return_pct"', content)
            self.assertIn('"t1_intraday_return_pct"', content)


if __name__ == "__main__":
    unittest.main()
