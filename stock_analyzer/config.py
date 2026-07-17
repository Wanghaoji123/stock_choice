from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"


@dataclass(frozen=True)
class Settings:
    data_dir: Path = DATA_DIR
    db_path: Path = DATA_DIR / "stock_analysis.sqlite3"
    request_timeout: float = 12.0
    page_size: int = 80
    max_candidates: int = 120
    news_candidates: int = 80
    news_per_stock: int = 8
    kline_days: int = 365
    top_n: int = 3
    use_sample_data: bool = False
    debug_urls: bool = False
    codes: tuple[str, ...] = ()
    excluded_prefixes: tuple[str, ...] = ("8", "4")
    excluded_name_tokens: tuple[str, ...] = ("ST", "*ST", "退")
    headers: dict[str, str] = field(
        default_factory=lambda: {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://quote.eastmoney.com/",
        }
    )
