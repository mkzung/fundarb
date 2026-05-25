"""Hyperliquid order wrapper.

Thin layer on top of the official ``hyperliquid-python-sdk`` ``Exchange``
client. The previous version reached for SDK helpers
(``get_market_open_order``, ``check_order_leverage``, ``get_limit_open_order``)
that don't exist in the public SDK, so the order paths blew up at runtime.
This module now uses ``Exchange.market_open`` / ``Exchange.market_close`` /
``Exchange.order`` directly and pulls perp metadata out of the correct slot of
``Info.meta_and_asset_ctxs()`` (``[meta_dict, ctx_list]``).
"""

try:
    from enum import StrEnum  # Python 3.11+
except ImportError:
    from enum import Enum

    class StrEnum(str, Enum):
        """Backport of StrEnum for Python < 3.11."""
        pass

import json


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


# Default slippage tolerance: 0.5%. Hard-coded 20% in the previous build was a
# foot-gun on a live mainnet account. Pass an explicit float to override.
DEFAULT_SLIPPAGE = 0.005


class HyperLiquidOrder(object):
    def __init__(self, address, info, exchange):
        """
        Parameters:
        address (str): The user's wallet address on the Hyperliquid platform.
        info (object): Hyperliquid SDK ``Info`` instance.
        exchange (object): Hyperliquid SDK ``Exchange`` instance.
        """
        self.address = address
        self.info = info
        self.exchange = exchange

    def _meta_universe(self):
        """Return the perp ``universe`` list from ``meta_and_asset_ctxs()``.

        The SDK call returns ``[meta_dict, ctx_list]``; ``universe`` lives on
        the meta dict at index 0. The previous code read index 1 which is the
        per-asset context array and has no ``universe`` key — that path
        raised ``KeyError`` on every order attempt.
        """
        meta_and_asset_ctxs = self.info.meta_and_asset_ctxs()
        meta_dict, _ctx_list = meta_and_asset_ctxs[0], meta_and_asset_ctxs[1]
        return meta_dict["universe"], _ctx_list

    def _mark_price(self, symbol: str) -> float:
        """Look up the current mark price for ``symbol`` from the asset-ctx list."""
        universe, ctx_list = self._meta_universe()
        for idx, asset in enumerate(universe):
            if asset["name"] == symbol:
                # Asset contexts are positionally aligned with universe entries.
                ctx = ctx_list[idx]
                if "markPx" in ctx:
                    return float(ctx["markPx"])
                if "midPx" in ctx and ctx["midPx"] is not None:
                    return float(ctx["midPx"])
                raise ValueError(f"No markPx/midPx available for symbol '{symbol}'.")
        raise ValueError(f"Symbol '{symbol}' not found in the Hyperliquid universe.")

    def create_market_order(
        self,
        symbol: str,
        sz_usd: float,
        side: Side,
        slippage: float = DEFAULT_SLIPPAGE,
    ):
        """Create a market order via ``Exchange.market_open``.

        Parameters:
        symbol (str): Asset symbol (e.g. ``"BTC"``).
        sz_usd (float): Order size denominated in USD.
        side (Side): ``Side.BUY`` or ``Side.SELL``.
        slippage (float): Tolerance fraction (default 0.5%).
        """
        if sz_usd <= 0:
            raise ValueError("sz_usd must be positive.")

        px = self._mark_price(symbol)
        sz = sz_usd / px

        is_buy = side == Side.BUY
        order_result = self.exchange.market_open(symbol, is_buy, sz, None, slippage)

        if isinstance(order_result, dict) and order_result.get("status") == "ok":
            for status in order_result["response"]["data"]["statuses"]:
                try:
                    filled = status["filled"]
                    print(
                        f'Hyperliquid Order #{filled["oid"]} filled {filled["totalSz"]} @{filled["avgPx"]}'
                    )
                except KeyError:
                    print(f'Error: {status.get("error", status)}')

        return order_result

    def create_limit_order(
        self,
        symbol: str,
        sz_usd: float,
        side: Side,
        limit_px: float,
    ):
        """Create a non-reduce-only GTC limit order via ``Exchange.order``."""
        if sz_usd <= 0:
            raise ValueError("sz_usd must be positive.")
        if limit_px <= 0:
            raise ValueError("limit_px must be positive.")

        sz = sz_usd / limit_px
        is_buy = side == Side.BUY
        order_type = {"limit": {"tif": "Gtc"}}

        order_result = self.exchange.order(
            symbol, is_buy, sz, limit_px, order_type, reduce_only=False
        )

        if isinstance(order_result, dict) and order_result.get("status") == "ok":
            for status in order_result["response"]["data"]["statuses"]:
                try:
                    print(
                        f'Hyperliquid Limit Order placed, status: {json.dumps(status)}'
                    )
                except KeyError:
                    print(f'Error: {status.get("error", status)}')

        return order_result

    def cancel_open_orders(self):
        """Cancel all open orders for the user on Hyperliquid."""
        open_orders = self.info.open_orders(self.address)
        cancel_requests = [
            {"coin": o["coin"], "oid": o["oid"]} for o in open_orders
        ]
        if not cancel_requests:
            print("No open orders to cancel.")
            return {"status": "ok", "response": {"data": {"statuses": []}}}

        cancel_response = self.exchange.bulk_cancel(cancel_requests)
        try:
            print(
                f'Cancelled all open orders, status: {json.dumps(cancel_response.get("response", cancel_response))}'
            )
        except Exception:
            print(f"Error cancelling orders: {cancel_response}")
        return cancel_response

    def market_close_an_asset(self, symbol: str):
        """Market-close an open position by symbol via ``Exchange.market_close``.

        Position lookup is by string symbol (Hyperliquid stores ``coin`` as
        the symbol string, e.g. ``"BTC"``, not an integer index — the prior
        code compared a string to ``symbol_info["index"]`` (an int) and never
        matched a position).
        """
        user_state = self.info.user_state(self.address)
        asset_positions = user_state.get("assetPositions", [])

        position = next(
            (
                p for p in asset_positions
                if p.get("position", {}).get("coin") == symbol
            ),
            None,
        )

        if position is None:
            print(f"No open position for {symbol} on Hyperliquid")
            return False

        size = float(position["position"]["szi"])
        side = "SELL" if size > 0 else "BUY"
        print(
            f"Closing {symbol} position of size {size} on Hyperliquid, side: {side}"
        )
        self.exchange.market_close(symbol)
        return True

    def get_open_positions(self):
        """Return a list of open positions with non-zero size."""
        user_state = self.info.user_state(self.address)
        filtered_positions = []
        for position in user_state.get("assetPositions", []):
            symbol = position["position"]["coin"]
            position_size = float(position["position"]["szi"])
            if position_size != 0:
                filtered_positions.append(
                    {"symbol": symbol, "position_size": position_size}
                )

        if len(filtered_positions) == 0:
            print("     No open positions")

        return filtered_positions
