import logging
from typing import Dict, Optional

import requests


class BackpackFundingRates:
    """
    Simple wrapper to fetch current funding rates from Backpack Exchange.

    Uses the public `GET /api/v1/markPrices` endpoint, which returns for each symbol:
      - symbol
      - fundingRate
      - markPrice
      - indexPrice
      - nextFundingTimestamp
    See official docs: https://api.backpack.exchange/api/v1/markPrices
    """

    BASE_URL = "https://api.backpack.exchange"

    def __init__(self, base_url: Optional[str] = None) -> None:
        self.base_url = base_url or self.BASE_URL

    def get_backpack_funding_rates(self) -> Dict[str, float]:
        """
        Fetches funding rates for all perpetual markets on Backpack.

        Returns:
            dict: { symbol_without_suffix: funding_rate_float }
                  e.g. {"BTC_USDC": 0.0001, ...}
        """
        url = f"{self.base_url}/api/v1/markPrices"

        try:
            resp = requests.get(url, timeout=5)
        except Exception as e:
            logging.error("Backpack markPrices request failed: %s", e)
            return {}

        if resp.status_code != 200:
            logging.error(
                "Backpack markPrices returned non-200 status: %s %s",
                resp.status_code,
                resp.text[:300],
            )
            return {}

        try:
            data = resp.json()
        except Exception as e:
            logging.error("Backpack markPrices JSON decode failed: %s; text=%r", e, resp.text[:300])
            return {}

        if not isinstance(data, list):
            logging.error("Backpack markPrices returned unexpected payload: %r", data)
            return {}

        rates: Dict[str, float] = {}
        for item in data:
            try:
                symbol = item.get("symbol")
                fr_str = item.get("fundingRate")
                if symbol is None or fr_str is None:
                    continue

                # Convert "BTC_USDC_PERP" -> "BTC_USDC" for consistency with other DEXes
                if symbol.endswith("_PERP"):
                    symbol_key = symbol.replace("_PERP", "")
                else:
                    symbol_key = symbol

                fr_val = float(fr_str)
                rates[symbol_key] = fr_val
            except Exception:
                # Skip malformed entries, but don't break the whole function
                continue

        return rates
