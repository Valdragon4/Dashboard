from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

try:
    import yfinance as yf
except Exception:  # pragma: no cover - dépendance optionnelle
    yf = None


class MarketPriceService:
    """Service de récupération de prix live avec cache par exécution."""

    _ISIN_REGEX = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")
    _SPECIAL_ISIN_TO_TICKER = {
        "XF000BTC0017": "BTC-USD",
        "XF000ETH0015": "ETH-USD",
    }

    def __init__(self) -> None:
        self._cache: Dict[str, Dict] = {}

    def get_price_for_isin(self, isin: str) -> Optional[Dict]:
        isin = (isin or "").strip().upper()
        if not isin or not self._ISIN_REGEX.match(isin):
            logger.warning("Market price skipped: invalid ISIN '%s'", isin)
            return None
        if isin in self._cache:
            logger.info("Market price cache hit for ISIN=%s", isin)
            return self._cache[isin]

        result = self._get_from_yfinance(isin) or self._get_from_fallback_api(isin)
        if result:
            self._cache[isin] = result
            logger.info(
                "Market price resolved ISIN=%s source=%s symbol=%s price=%s",
                isin,
                result.get("source"),
                result.get("symbol"),
                result.get("price"),
            )
        else:
            logger.error("Market price resolution failed for ISIN=%s", isin)
        return result

    def _make_result(self, *, price: Decimal, source: str, symbol: str | None = None) -> Dict:
        return {
            "price": price,
            "source": source,
            "symbol": symbol,
            "priced_at": datetime.now(timezone.utc).isoformat(),
        }

    def _get_from_yfinance(self, isin: str) -> Optional[Dict]:
        if yf is None:
            return None
        try:
            resolved_symbol = self._resolve_yahoo_symbol_from_isin(isin)
            candidates = []
            special_ticker = self._SPECIAL_ISIN_TO_TICKER.get(isin)
            if special_ticker:
                candidates.append(special_ticker)
            if resolved_symbol:
                candidates.append(resolved_symbol)
            candidates.extend([isin, f"{isin}.PA", f"{isin}.DE", f"{isin}.AS", f"{isin}.L"])
            for ticker in candidates:
                tk = yf.Ticker(ticker)
                fast_info = getattr(tk, "fast_info", {}) or {}
                last_price = fast_info.get("lastPrice") or fast_info.get("last_price")
                if last_price is None:
                    hist = tk.history(period="1d")
                    if not hist.empty:
                        last_price = hist["Close"].iloc[-1]
                if last_price is None:
                    continue
                price = Decimal(str(last_price))
                return self._make_result(price=price, source="yfinance", symbol=ticker)
        except Exception as exc:
            logger.warning("yfinance pricing failure for %s: %s", isin, exc)
        return None

    def _resolve_yahoo_symbol_from_isin(self, isin: str) -> Optional[str]:
        try:
            response = requests.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={"q": isin, "quotesCount": 5, "newsCount": 0},
                timeout=10.0,
            )
            response.raise_for_status()
            payload = response.json()
            quotes = payload.get("quotes") or []
            for quote in quotes:
                symbol = quote.get("symbol")
                if symbol:
                    return symbol
        except Exception as exc:
            logger.debug("yahoo symbol resolution failed for %s: %s", isin, exc)
        return None

    def _get_from_fallback_api(self, isin: str) -> Optional[Dict]:
        alpha_key = os.getenv("ALPHA_VANTAGE_API_KEY")
        polygon_key = os.getenv("POLYGON_API_KEY")

        if alpha_key:
            alpha_price = self._get_from_alpha_vantage(isin, alpha_key)
            if alpha_price:
                return alpha_price
        if polygon_key:
            polygon_price = self._get_from_polygon(isin, polygon_key)
            if polygon_price:
                return polygon_price
        return None

    def _get_from_alpha_vantage(self, isin: str, api_key: str) -> Optional[Dict]:
        try:
            response = requests.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "SYMBOL_SEARCH",
                    "keywords": isin,
                    "apikey": api_key,
                },
                timeout=15.0,
            )
            response.raise_for_status()
            payload = response.json()
            best_matches = payload.get("bestMatches") or []
            symbol = (best_matches[0] or {}).get("1. symbol") if best_matches else None
            if not symbol:
                return None

            quote = requests.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "GLOBAL_QUOTE",
                    "symbol": symbol,
                    "apikey": api_key,
                },
                timeout=15.0,
            )
            quote.raise_for_status()
            quote_payload = quote.json()
            price_raw = (quote_payload.get("Global Quote") or {}).get("05. price")
            if not price_raw:
                return None
            return self._make_result(
                price=Decimal(str(price_raw)),
                source="alpha_vantage",
                symbol=symbol,
            )
        except (requests.RequestException, InvalidOperation, ValueError) as exc:
            logger.warning("alpha vantage pricing failure for %s: %s", isin, exc)
            return None

    def _get_from_polygon(self, isin: str, api_key: str) -> Optional[Dict]:
        try:
            ticker_response = requests.get(
                "https://api.polygon.io/v3/reference/tickers",
                params={
                    "search": isin,
                    "active": "true",
                    "limit": 1,
                    "apiKey": api_key,
                },
                timeout=15.0,
            )
            ticker_response.raise_for_status()
            ticker_payload = ticker_response.json()
            results = ticker_payload.get("results") or []
            symbol = (results[0] or {}).get("ticker") if results else None
            if not symbol:
                return None

            price_response = requests.get(
                f"https://api.polygon.io/v2/last/trade/{symbol}",
                params={"apiKey": api_key},
                timeout=15.0,
            )
            price_response.raise_for_status()
            price_payload = price_response.json()
            price_raw = (price_payload.get("results") or {}).get("p")
            if price_raw is None:
                return None
            return self._make_result(
                price=Decimal(str(price_raw)),
                source="polygon",
                symbol=symbol,
            )
        except (requests.RequestException, InvalidOperation, ValueError) as exc:
            logger.warning("polygon pricing failure for %s: %s", isin, exc)
            return None
