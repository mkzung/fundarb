import logging
import os
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class OrderlyFundingRates:
    """Fetch and normalize funding rates from Orderly.

    This implementation only uses the *public* REST endpoint
    `/v1/public/funding_rates`, so it works without any authentication
    and is safe for both testnet and mainnet.

    It returns a mapping of the form:
        { "ETH": 0.0001, "BTC": -0.0002, ... }
    where keys are the underlying symbols (without PERP/USDC suffixes).
    """

    def __init__(self, env: Optional[str] = None) -> None:
        # Detect environment: "testnet" (default) vs "mainnet"
        env_from_os = (env or os.getenv("ORDERLY_ENV", "testnet")).lower()
        if env_from_os not in ("testnet", "mainnet"):
            env_from_os = "testnet"

        self.env = env_from_os
        if self.env == "mainnet":
            # Mainnet REST base
            self.base_url = "https://api-evm.orderly.org"
        else:
            # Testnet REST base
            self.base_url = "https://testnet-api-evm.orderly.org"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_orderly_funding_rates(self) -> Dict[str, float]:
        """Return current funding rates on Orderly as {symbol -> rate}.

        If anything goes wrong (network error, unexpected payload, etc.),
        an empty dict is returned and the caller can treat Orderly as
        "no data" for this run.
        """

        try:
            raw_items = self._request_funding_rates()
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to fetch Orderly funding rates: %s", exc)
            return {}

        result: Dict[str, float] = {}
        for row in raw_items:
            symbol_raw = self._extract_symbol(row)
            if not symbol_raw:
                continue

            underlying = self._normalize_symbol(symbol_raw)
            if not underlying:
                continue

            rate = self._extract_rate(row)
            if rate is None:
                continue

            # underlying типа "ETH", "BTC", "SOL" и т.п.
            result[underlying] = rate

        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _request_funding_rates(self) -> List[Dict[str, Any]]:
        """Low-level HTTP call to Orderly `funding_rates` endpoint.

        The official EVM API exposes:
            GET /v1/public/funding_rates

        Typical response shape (simplified):

            {
              "success": true,
              "data": [
                {
                  "symbol": "PERP_BTC_USDC",
                  "est_funding_rate": "0.0001",
                  "last_funding_rate": "0.00005",
                  ...
                },
                ...
              ]
            }

        We try to be tolerant to small schema changes.
        """
        url = f"{self.base_url}/v1/public/funding_rates"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        payload = resp.json()

        # Try to find the list of rows in a robust way.
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]

        if not isinstance(payload, dict):
            return []

        # Common patterns: {"data": [...]} or {"rows": [...]}
        for key in ("data", "rows", "funding_rates", "result"):
            maybe = payload.get(key)
            if isinstance(maybe, list):
                return [row for row in maybe if isinstance(row, dict)]

        # Fallback: if payload itself *looks* like a single row
        if "symbol" in payload:
            return [payload]  # type: ignore[list-item]

        return []

    @staticmethod
    def _extract_symbol(row: Dict[str, Any]) -> Optional[str]:
        sym = row.get("symbol") or row.get("symbolName") or row.get("market")
        if not isinstance(sym, str):
            return None
        return sym.strip()

    @staticmethod
    def _normalize_symbol(symbol: str) -> Optional[str]:
        """Convert Orderly symbol to a generic underlying symbol.

        Examples:
            PERP_ETH_USDC -> ETH
            PERP_BTC_USDT -> BTC
            ETH-PERP -> ETH
        """
        if not symbol:
            return None

        s = symbol.upper()

        # Common Orderly format: PERP_ETH_USDC / PERP_BTC_USDC
        if s.startswith("PERP_"):
            s = s[len("PERP_") :]

        # Strip common quote currencies
        for suffix in ("_USDC", "_USDT", "-USDC", "-USDT"):
            if s.endswith(suffix):
                s = s[: -len(suffix)]
                break

        # Other style: ETH-PERP, BTC-PERP, etc.
        for suffix in ("-PERP", "_PERP"):
            if s.endswith(suffix):
                s = s[: -len(suffix)]
                break

        s = s.strip("_- ")
        if not s:
            return None
        return s

    @staticmethod
    def _extract_rate(row: Dict[str, Any]) -> Optional[float]:
        """Extract a funding rate from a row.

        Different versions of the API have used slightly different
        field names, so we check several in order of preference.
        """
        candidates = [
            "est_funding_rate",
            "funding_rate",
            "funding_rate_8h",
            "last_funding_rate",
            "predicted_rate",
        ]

        value: Optional[float] = None
        for key in candidates:
            raw = row.get(key)
            if raw is None:
                continue
            try:
                # Values are usually strings like "0.0001"
                value = float(raw)
                break
            except (TypeError, ValueError):
                continue

        return value
