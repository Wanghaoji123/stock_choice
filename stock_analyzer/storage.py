from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from .models import CapitalFlow, KLine, NewsItem, Recommendation, StockQuote


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS quotes (
                code TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (code, fetched_at)
            );
            CREATE TABLE IF NOT EXISTS klines (
                code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (code, trade_date)
            );
            CREATE TABLE IF NOT EXISTS news (
                code TEXT NOT NULL,
                url TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (code, url)
            );
            CREATE TABLE IF NOT EXISTS recommendations (
                run_date TEXT NOT NULL,
                rank INTEGER NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (run_date, rank)
            );
            CREATE TABLE IF NOT EXISTS capital_flows (
                code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (code, trade_date)
            );
            """
        )
        self.conn.commit()

    def save_quotes(self, quotes: Iterable[StockQuote]) -> None:
        rows = [
            (
                quote.code,
                quote.fetched_at.isoformat(),
                json.dumps(quote.__dict__, default=str, ensure_ascii=False),
            )
            for quote in quotes
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO quotes(code, fetched_at, payload) VALUES(?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def save_klines(self, rows: Iterable[KLine]) -> None:
        payloads = [
            (
                item.code,
                item.trade_date.isoformat(),
                json.dumps(item.__dict__, default=str, ensure_ascii=False),
            )
            for item in rows
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO klines(code, trade_date, payload) VALUES(?, ?, ?)",
            payloads,
        )
        self.conn.commit()

    def save_news(self, rows: Iterable[NewsItem]) -> None:
        payloads = [
            (item.code, item.url, json.dumps(item.__dict__, default=str, ensure_ascii=False))
            for item in rows
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO news(code, url, payload) VALUES(?, ?, ?)",
            payloads,
        )
        self.conn.commit()

    def save_capital_flows(self, trade_date: str, rows: Iterable[CapitalFlow]) -> None:
        payloads = [
            (
                item.code,
                trade_date,
                item.fetched_at.isoformat(),
                json.dumps(item.__dict__, default=str, ensure_ascii=False),
            )
            for item in rows
        ]
        self.conn.executemany(
            """INSERT OR REPLACE INTO capital_flows(code, trade_date, fetched_at, payload)
               VALUES(?, ?, ?, ?)""",
            payloads,
        )
        self.conn.commit()

    def save_recommendations(self, run_date: str, rows: list[Recommendation]) -> None:
        self.conn.execute("DELETE FROM recommendations WHERE run_date = ?", (run_date,))
        payloads = [
            (run_date, rank, json.dumps(item.as_row(), ensure_ascii=False))
            for rank, item in enumerate(rows, start=1)
        ]
        self.conn.executemany(
            "INSERT INTO recommendations(run_date, rank, payload) VALUES(?, ?, ?)",
            payloads,
        )
        self.conn.commit()
