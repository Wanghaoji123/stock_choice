from __future__ import annotations

import json
import re
import subprocess
import time
import concurrent.futures
from html import unescape
from http.client import HTTPException
from datetime import date, datetime, timedelta
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import Settings
from .models import CapitalFlow, IntradayPoint, KLine, NewsItem, StockQuote


def _to_float(value: object) -> float | None:
    if value in (None, "-", ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text[: len(fmt)], fmt)
        except ValueError:
            continue
    return None


def eastmoney_market(code: str) -> str:
    return "sh" if code.startswith(("5", "6", "9")) else "sz"


def eastmoney_secid(code: str) -> str:
    return f"{1 if eastmoney_market(code) == 'sh' else 0}.{code}"


def build_url(url: str, params: dict[str, object]) -> str:
    return f"{url}?{urlencode(params)}"


class EastMoneyClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.bulk_capital_flows: dict[str, CapitalFlow] = {}
        self.capital_flow_stats = {
            "requests": 0,
            "success": 0,
            "empty": 0,
            "error": 0,
            "stale": 0,
        }

    def _fetch_qq_capital_flow(self, code: str) -> CapitalFlow | None:
        symbol = f"{'sh' if code.startswith(('5', '6', '9')) else 'sz'}{code}"
        url = (
            "https://proxy.finance.qq.com/cgi/cgi-bin/fundflow/hsfundtab"
            f"?code={symbol}&type=todayFundFlow&klineNeedDay=1"
        )
        try:
            result = subprocess.run(
                [
                    "curl", "-L", "--compressed", "--silent", "--show-error",
                    "--max-time", str(int(self.settings.qq_fundflow_request_timeout)),
                    "-H", "Referer: https://gu.qq.com/", url,
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.qq_fundflow_request_timeout + 2,
            )
            payload = json.loads(result.stdout)
            row = (payload.get("data") or {}).get("todayFundFlow") or {}
            main = int(row["mainNetIn"])
            extra_large = int(row["superFlow"])
            large = int(row["bigFlow"])
            medium = int(row["normalFlow"])
            small = int(row["smallFlow"])
            if abs(main - extra_large - large) > 2:
                return None
            return CapitalFlow(
                code=code,
                fetched_at=datetime.now(),
                main_net=float(main),
                small_net=float(small),
                medium_net=float(medium),
                large_net=float(large),
                extra_large_net=float(extra_large),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError, subprocess.SubprocessError):
            return None

    def fetch_qq_capital_flows(self, codes: list[str]) -> dict[str, CapitalFlow]:
        """并发拉取腾讯当日四档资金流，整批超时或覆盖率不足时失败。"""
        unique_codes = list(dict.fromkeys(codes))
        print(
            f"正在通过腾讯财经拉取 {len(unique_codes)} 只股票的当日资金流"
            f"（{self.settings.qq_fundflow_workers}并发，整批硬超时"
            f" {self.settings.qq_fundflow_batch_timeout:.0f} 秒）..."
        )
        started = time.monotonic()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.settings.qq_fundflow_workers)
        futures = {executor.submit(self._fetch_qq_capital_flow, code): code for code in unique_codes}
        flows: dict[str, CapitalFlow] = {}
        timed_out = False
        try:
            for future in concurrent.futures.as_completed(
                futures, timeout=self.settings.qq_fundflow_batch_timeout
            ):
                flow = future.result()
                if flow is not None:
                    flows[flow.code] = flow
        except TimeoutError:
            timed_out = True
        finally:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=not timed_out, cancel_futures=True)
        elapsed = time.monotonic() - started
        coverage = len(flows) / len(unique_codes) if unique_codes else 0.0
        print(f"腾讯资金流：成功 {len(flows)}/{len(unique_codes)} ({coverage:.1%})，耗时 {elapsed:.1f} 秒。")
        if timed_out:
            raise RuntimeError(
                f"腾讯资金流整批超过 {self.settings.qq_fundflow_batch_timeout:.0f} 秒，任务已停止"
            )
        if len(unique_codes) >= 20 and coverage < 0.8:
            raise RuntimeError(f"腾讯资金流成功率仅 {coverage:.1%}，低于80%，任务已停止")
        self.bulk_capital_flows = flows
        return flows

    def _get_json(self, url: str, params: dict[str, object]) -> dict:
        full_url = build_url(url, params)
        if self.settings.debug_urls:
            print(f"  GET {full_url}")
        raw = self._fetch_text(full_url, referer=self.settings.headers.get("Referer"))
        if raw.startswith("jQuery"):
            raw = raw[raw.find("(") + 1 : raw.rfind(")")]
        return json.loads(raw)

    def _get_text(self, url: str) -> str:
        if self.settings.debug_urls:
            print(f"  GET {url}")
        return self._fetch_text(url, referer="https://guba.eastmoney.com/")

    def _fetch_text(self, url: str, referer: str | None = None) -> str:
        request_headers = dict(self.settings.headers)
        if referer:
            request_headers["Referer"] = referer
        request = Request(url, headers=request_headers)
        try:
            with urlopen(request, timeout=self.settings.request_timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except (OSError, HTTPException, URLError) as urllib_exc:
            return self._fetch_text_with_curl(url, request_headers, urllib_exc)

    def _fetch_text_with_curl(
        self,
        url: str,
        headers: dict[str, str],
        original_error: BaseException,
    ) -> str:
        cmd = [
            "curl",
            "-L",
            "--compressed",
            "--silent",
            "--show-error",
            "--max-time",
            str(int(self.settings.request_timeout)),
        ]
        for key, value in headers.items():
            cmd.extend(["-H", f"{key}: {value}"])
        cmd.append(url)
        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.CalledProcessError) as curl_exc:
            raise RuntimeError(f"请求失败: urllib={original_error}; curl={curl_exc}") from curl_exc
        if not result.stdout.strip():
            raise RuntimeError(f"请求失败: urllib={original_error}; curl 返回空内容")
        return result.stdout

    def fetch_a_share_quotes(self) -> list[StockQuote]:
        """全市场行情固定走新浪，避免先等待不可用的东方财富接口。"""
        return self._fetch_sina_a_share_quotes()

    def fetch_quote(self, code: str, name: str | None = None) -> StockQuote | None:
        return self._fetch_sina_quote(code, name)

    def _quote_from_eastmoney_item(
        self,
        item: dict,
        fetched_at: datetime,
        fallback_code: str | None = None,
        fallback_name: str | None = None,
    ) -> StockQuote | None:
        code = str(item.get("f12") or fallback_code or "")
        name = str(item.get("f14") or fallback_name or code)
        if not code or self._excluded(code, name):
            return None
        flow_values = {
            "main_net": _to_float(item.get("f62")),
            "extra_large_net": _to_float(item.get("f66")),
            "large_net": _to_float(item.get("f72")),
            "medium_net": _to_float(item.get("f78")),
            "small_net": _to_float(item.get("f84")),
        }
        if all(value is not None for value in flow_values.values()):
            self.bulk_capital_flows[code] = CapitalFlow(
                code=code,
                fetched_at=fetched_at,
                main_net=flow_values["main_net"],
                small_net=flow_values["small_net"],
                medium_net=flow_values["medium_net"],
                large_net=flow_values["large_net"],
                extra_large_net=flow_values["extra_large_net"],
            )
        return StockQuote(
            code=code,
            name=name,
            market=eastmoney_market(code),
            price=_to_float(item.get("f2")),
            pct_chg=_to_float(item.get("f3")),
            volume=_to_float(item.get("f5")),
            amount=_to_float(item.get("f6")),
            turnover_rate=_to_float(item.get("f8")),
            market_cap=_to_float(item.get("f20")),
            fetched_at=fetched_at,
            open_price=_to_float(item.get("f17")),
            high_price=_to_float(item.get("f15")),
            low_price=_to_float(item.get("f16")),
            previous_close=_to_float(item.get("f18")),
        )

    def _fetch_eastmoney_quote(self, code: str, name: str | None = None) -> StockQuote | None:
        payload = self._get_json(
            "https://push2.eastmoney.com/api/qt/stock/get",
            {
                "secid": eastmoney_secid(code),
                "fields": "f12,f14,f2,f3,f5,f6,f8,f15,f16,f17,f18,f20,f62,f66,f72,f78,f84",
                "fltt": 2,
                "invt": 2,
            },
        )
        data = payload.get("data") or {}
        if not data:
            return None
        return self._quote_from_eastmoney_item(data, datetime.now(), code, name)

    def _fetch_sina_quote(self, code: str, name: str | None = None) -> StockQuote | None:
        symbol = f"{eastmoney_market(code)}{code}"
        raw = self._fetch_text(
            f"https://hq.sinajs.cn/list={symbol}",
            referer="https://finance.sina.com.cn/",
        )
        match = re.search(r'="(?P<data>.*)"', raw)
        if not match:
            return None
        parts = match.group("data").split(",")
        if len(parts) < 32 or not parts[0]:
            return None
        previous_close = _to_float(parts[2])
        current_price = _to_float(parts[3])
        pct_chg = None
        if current_price is not None and previous_close:
            pct_chg = (current_price - previous_close) / previous_close * 100
        volume = _to_float(parts[8])
        amount = _to_float(parts[9])
        source_time = datetime.now()
        if len(parts) > 31:
            try:
                source_time = datetime.strptime(f"{parts[30]} {parts[31]}", "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
        return StockQuote(
            code=code,
            name=name or parts[0] or code,
            market=eastmoney_market(code),
            price=current_price,
            pct_chg=pct_chg,
            volume=volume,
            amount=amount,
            turnover_rate=None,
            market_cap=None,
            fetched_at=source_time,
            open_price=_to_float(parts[1]),
            high_price=_to_float(parts[4]),
            low_price=_to_float(parts[5]),
            previous_close=previous_close,
        )

    def _fetch_eastmoney_a_share_quotes(self) -> list[StockQuote]:
        fields = "f12,f14,f2,f3,f5,f6,f8,f15,f16,f17,f18,f20,f62,f66,f72,f78,f84"
        fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
        quotes: list[StockQuote] = []
        fetched_at = datetime.now()
        pages = max(1, (self.settings.max_candidates + self.settings.page_size - 1) // self.settings.page_size)
        for page in range(1, pages + 1):
            payload = self._get_json(
                "https://push2.eastmoney.com/api/qt/clist/get",
                {
                    "pn": page,
                    "pz": self.settings.page_size,
                    "po": 1,
                    "np": 1,
                    "fltt": 2,
                    "invt": 2,
                    "fid": "f6",
                    "fs": fs,
                    "fields": fields,
                },
            )
            rows = (payload.get("data") or {}).get("diff") or []
            for item in rows:
                quote = self._quote_from_eastmoney_item(item, fetched_at)
                if quote is not None:
                    quotes.append(quote)
            time.sleep(0.15)
        return quotes[: self.settings.max_candidates]

    def _fetch_sina_a_share_quotes(self) -> list[StockQuote]:
        quotes: list[StockQuote] = []
        fetched_at = datetime.now()
        page_size = 100
        pages = max(1, (self.settings.max_candidates + page_size - 1) // page_size)

        def fetch_page(page: int) -> list[dict]:
            url = build_url(
                "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData",
                {
                    "page": page,
                    "num": page_size,
                    "sort": "amount",
                    "asc": 0,
                    "node": "hs_a",
                    "symbol": "",
                    "_s_r_a": "page",
                },
            )
            if self.settings.debug_urls:
                print(f"  GET {url}")
            result = subprocess.run(
                [
                    "curl", "-L", "--compressed", "--silent", "--show-error",
                    "--max-time", str(int(self.settings.request_timeout)),
                    "-H", "Referer: https://finance.sina.com.cn/", url,
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.request_timeout + 2,
            )
            rows = json.loads(result.stdout)
            if not isinstance(rows, list):
                return []
            return rows

        print(f"新浪行情并发拉取 {pages} 页（单页硬超时 {self.settings.request_timeout:.0f} 秒）...")
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=min(12, pages))
        future_pages = {executor.submit(fetch_page, page): page for page in range(1, pages + 1)}
        page_rows: dict[int, list[dict]] = {}
        try:
            for future in concurrent.futures.as_completed(future_pages, timeout=30):
                page = future_pages[future]
                try:
                    page_rows[page] = future.result()
                except Exception:
                    page_rows[page] = []
        except TimeoutError:
            pass
        finally:
            for future in future_pages:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)

        for page in sorted(page_rows):
            rows = page_rows[page]
            for item in rows:
                code = str(item.get("code") or "")
                name = str(item.get("name") or "")
                if not code or self._excluded(code, name):
                    continue
                symbol = str(item.get("symbol") or "")
                quotes.append(
                    StockQuote(
                        code=code,
                        name=name,
                        market="sh" if symbol.startswith("sh") else "sz",
                        price=_to_float(item.get("trade")),
                        pct_chg=_to_float(item.get("changepercent")),
                        volume=_to_float(item.get("volume")),
                        amount=_to_float(item.get("amount")),
                        turnover_rate=_to_float(item.get("turnoverratio")),
                        market_cap=_to_float(item.get("mktcap")),
                        fetched_at=fetched_at,
                        open_price=_to_float(item.get("open")),
                        high_price=_to_float(item.get("high")),
                        low_price=_to_float(item.get("low")),
                        previous_close=_to_float(item.get("settlement")),
                    )
                )
                if len(quotes) >= self.settings.max_candidates:
                    return quotes
        if len(quotes) < min(20, self.settings.max_candidates):
            raise RuntimeError(f"新浪全市场行情仅返回 {len(quotes)} 只，任务已停止")
        return quotes[: self.settings.max_candidates]

    def fetch_capital_flow(self, code: str) -> CapitalFlow | None:
        """获取当日超大/大/中/小单净流入。接口无盘中数据时返回 None。"""
        self.capital_flow_stats["requests"] += 1
        try:
            payload = self._get_json(
                "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
                {
                    "secid": eastmoney_secid(code),
                    "klt": 101,
                    "lmt": 1,
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                },
            )
        except Exception:
            self.capital_flow_stats["error"] += 1
            return None
        rows = (payload.get("data") or {}).get("klines") or []
        if not rows:
            self.capital_flow_stats["empty"] += 1
            return None
        parts = str(rows[-1]).split(",")
        if len(parts) < 6:
            self.capital_flow_stats["error"] += 1
            return None
        try:
            flow_date = datetime.strptime(parts[0], "%Y-%m-%d").date()
        except ValueError:
            self.capital_flow_stats["error"] += 1
            return None
        if flow_date != date.today():
            self.capital_flow_stats["stale"] += 1
            return None
        values = [_to_float(value) for value in parts[1:6]]
        if any(value is None for value in values):
            self.capital_flow_stats["error"] += 1
            return None
        self.capital_flow_stats["success"] += 1
        flow = CapitalFlow(
            code=code,
            fetched_at=datetime.now(),
            main_net=values[0],
            small_net=values[1],
            medium_net=values[2],
            large_net=values[3],
            extra_large_net=values[4],
        )
        self.bulk_capital_flows[code] = flow
        return flow

    def fetch_intraday_trends(self, code: str) -> list[IntradayPoint]:
        """获取分时价格、均价和成交，用于识别冲高滞涨与回落。"""
        try:
            payload = self._get_json(
                "https://push2his.eastmoney.com/api/qt/stock/trends2/get",
                {
                    "secid": eastmoney_secid(code),
                    "ndays": 1,
                    "iscr": 0,
                    "iscca": 0,
                    "fields1": "f1,f2,f3,f5,f6,f7,f8,f9,f10,f11,f12,f13",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                },
            )
        except Exception:
            return []
        rows: list[IntradayPoint] = []
        for raw in (payload.get("data") or {}).get("trends") or []:
            parts = str(raw).split(",")
            if len(parts) < 2:
                continue
            try:
                timestamp = datetime.strptime(parts[0], "%Y-%m-%d %H:%M")
                price = float(parts[1])
            except ValueError:
                continue
            rows.append(
                IntradayPoint(
                    timestamp=timestamp,
                    price=price,
                    average_price=_to_float(parts[2]) if len(parts) > 2 else None,
                    volume=_to_float(parts[3]) if len(parts) > 3 else None,
                    amount=_to_float(parts[4]) if len(parts) > 4 else None,
                )
            )
        return rows

    def _excluded(self, code: str, name: str) -> bool:
        if code.startswith(self.settings.excluded_prefixes):
            return True
        upper_name = name.upper()
        return any(token.upper() in upper_name for token in self.settings.excluded_name_tokens)

    def fetch_kline(self, code: str) -> list[KLine]:
        return self._fetch_sina_kline(code)

    def _fetch_eastmoney_kline(self, code: str) -> list[KLine]:
        end = date.today()
        begin = end - timedelta(days=self.settings.kline_days + 30)
        payload = self._get_json(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            {
                "secid": eastmoney_secid(code),
                "klt": 101,
                "fqt": 1,
                "beg": begin.strftime("%Y%m%d"),
                "end": end.strftime("%Y%m%d"),
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            },
        )
        raw_rows = (payload.get("data") or {}).get("klines") or []
        rows: list[KLine] = []
        for raw in raw_rows[-self.settings.kline_days :]:
            parts = raw.split(",")
            if len(parts) < 11:
                continue
            rows.append(
                KLine(
                    code=code,
                    trade_date=datetime.strptime(parts[0], "%Y-%m-%d").date(),
                    open=float(parts[1]),
                    close=float(parts[2]),
                    high=float(parts[3]),
                    low=float(parts[4]),
                    volume=float(parts[5]),
                    amount=float(parts[6]),
                    amplitude=_to_float(parts[7]),
                    pct_chg=_to_float(parts[8]),
                    turnover_rate=_to_float(parts[10]),
                )
            )
        return rows

    def _fetch_sina_kline(self, code: str) -> list[KLine]:
        symbol = f"{eastmoney_market(code)}{code}"
        payload = self._get_text(
            build_url(
                "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_=/CN_MarketData.getKLineData",
                {
                    "symbol": symbol,
                    "scale": 240,
                    "ma": "no",
                    "datalen": self.settings.kline_days,
                },
            )
        )
        match = re.search(r"=\((\[.*\])\);?", payload, re.S)
        if not match:
            raise RuntimeError("新浪 K 线接口未返回可解析数据")
        try:
            raw_rows = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"新浪 K 线 JSON 解析失败: {exc}") from exc
        rows: list[KLine] = []
        previous_close: float | None = None
        for item in raw_rows[-self.settings.kline_days :]:
            close = float(item["close"])
            volume = float(item.get("volume") or 0)
            pct_chg = None
            if previous_close:
                pct_chg = (close - previous_close) / previous_close * 100
            previous_close = close
            rows.append(
                KLine(
                    code=code,
                    trade_date=datetime.strptime(item["day"], "%Y-%m-%d").date(),
                    open=float(item["open"]),
                    close=close,
                    high=float(item["high"]),
                    low=float(item["low"]),
                    volume=volume,
                    amount=close * volume,
                    amplitude=None,
                    pct_chg=pct_chg,
                    turnover_rate=None,
                )
            )
        return rows

    def fetch_news(self, code: str, name: str) -> list[NewsItem]:
        news = self._fetch_search_news(code, name)
        if news:
            return news
        return self._fetch_guba_news(code, name)

    def _fetch_search_news(self, code: str, name: str) -> list[NewsItem]:
        rows = []
        for search_type in (8192, 14, -1):
            try:
                payload = self._get_json(
                    "https://searchapi.eastmoney.com/business/Web/GetSearchList",
                    {
                        "keyword": f"{code} {name}",
                        "type": search_type,
                        "pageindex": 1,
                        "pagesize": self.settings.news_per_stock,
                    },
                )
            except RuntimeError:
                continue
            if not isinstance(payload, dict):
                continue
            rows = payload.get("Data") or payload.get("data") or []
            if isinstance(rows, dict):
                rows = rows.get("List") or rows.get("list") or []
            if rows:
                break
        news: list[NewsItem] = []
        for item in rows[: self.settings.news_per_stock]:
            title = str(item.get("Title") or item.get("title") or "").strip()
            if not title:
                continue
            url = str(item.get("Url") or item.get("url") or item.get("ArticleUrl") or "")
            source = str(item.get("Source") or item.get("source") or "东方财富")
            published = _parse_datetime(
                item.get("ShowTime")
                or item.get("showTime")
                or item.get("PublishTime")
                or item.get("publishTime")
            )
            summary = str(item.get("Content") or item.get("content") or item.get("Summary") or "")
            news.append(
                NewsItem(
                    code=code,
                    title=title,
                    url=url or f"https://so.eastmoney.com/news/s?keyword={code}",
                    source=source,
                    published_at=published,
                    summary=summary,
                )
            )
        return news

    def _fetch_guba_news(self, code: str, name: str) -> list[NewsItem]:
        html = self._get_text(f"https://guba.eastmoney.com/list,{code}.html")
        news = self._parse_guba_article_list(html, code)
        if news:
            return news[: self.settings.news_per_stock]
        return self._parse_guba_table(html, code, name)[: self.settings.news_per_stock]

    def _parse_guba_article_list(self, html: str, code: str) -> list[NewsItem]:
        match = re.search(r"var\s+article_list\s*=\s*(\{.*?\})\s*;\s*var\s+other_list", html, re.S)
        if not match:
            return []
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            return []
        rows = payload.get("re") or []
        news: list[NewsItem] = []
        for item in rows:
            title = str(item.get("post_title") or "").strip()
            if not title:
                continue
            post_id = item.get("post_id")
            url = str(item.get("art_unique_url") or "")
            if not url and post_id:
                url = f"https://guba.eastmoney.com/news,{code},{post_id}.html"
            news.append(
                NewsItem(
                    code=code,
                    title=title,
                    url=url or f"https://guba.eastmoney.com/list,{code}.html",
                    source=str(item.get("user_nickname") or "东方财富股吧"),
                    published_at=_parse_datetime(item.get("post_publish_time") or item.get("post_display_time")),
                    summary=str(item.get("post_abstract") or item.get("post_content") or ""),
                )
            )
        return news

    def _parse_guba_table(self, html: str, code: str, name: str) -> list[NewsItem]:
        pattern = re.compile(
            r'<a[^>]+data-postid="(?P<post_id>\d+)"[^>]+href="(?P<href>[^"]+)"[^>]*>'
            r"(?P<title>.*?)</a>.*?<div class=\"update\">(?P<time>.*?)</div>",
            re.S,
        )
        news: list[NewsItem] = []
        for match in pattern.finditer(html):
            title = re.sub(r"<.*?>", "", match.group("title"))
            title = unescape(title).strip()
            if not title:
                continue
            href = unescape(match.group("href"))
            if href.startswith("//"):
                url = f"https:{href}"
            elif href.startswith("/"):
                url = f"https://guba.eastmoney.com{href}"
            else:
                url = href
            published_at = _parse_datetime(f"{datetime.now().year}-{match.group('time')}")
            news.append(
                NewsItem(
                    code=code,
                    title=title,
                    url=url,
                    source=f"{name}股吧",
                    published_at=published_at,
                    summary="",
                )
            )
        return news
