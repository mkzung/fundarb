try:
    from enum import StrEnum  # Python 3.11+
except ImportError:
    from enum import Enum

    class StrEnum(str, Enum):
        """Backport of StrEnum for Python < 3.11."""
        pass

from hyperliquid.utils import constants
import hyperliq_utils as hyperliq_utils
import json


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class HyperLiquidOrder(object):
    def __init__(self, address, info, exchange):
        """
        Parameters:
        address (str): The user's wallet address on the Hyperliquid platform.
        info (object): An object to interact with Hyperliquid's API.
        exchange (object): An object representing the exchange for order-related operations.
        """
        self.address = address
        self.info = info
        self.exchange = exchange

    def create_market_order(
        self,
        symbol: str,
        sz_usd: float,
        side: Side,
        slippage: float = 0.2,
    ):
        """
        Create a market order.

        Parameters:
        symbol (str): The symbol/ticker of the asset to trade.
        sz_usd (float): The size of the order in USD.
        side (Side): The side of the trade (BUY or SELL).
        slippage (float): Slippage tolerance factor.

        Returns:
        dict: The response from the Hyperliquid API after placing the order.
        """
        meta_and_asset_ctxs = self.info.meta_and_asset_ctxs()
        universe = meta_and_asset_ctxs[1]["universe"]

        symbol_info = next((asset for asset in universe if asset["name"] == symbol), None)
        if symbol_info is None:
            raise ValueError(f"Symbol '{symbol}' not found in the Hyperliquid universe.")

        px = float(symbol_info["markPx"])
        max_px = round(px * (1 + slippage), 8)
        min_px = round(px * (1 - slippage), 8)

        sz = sz_usd / px

        order_spec = hyperliq_utils.get_market_open_order(
            symbol,
            sz,
            side.value,
            {"limitPx": max_px if side == Side.BUY else min_px},
        )
        hyperliq_utils.check_order_leverage(
            self.info.user_state(self.address), symbol, sz, side.value
        )
        order_result = self.exchange.order(order_spec, {"gasPrice": "0"})

        # print out the status of the order
        if order_result["status"] == "ok":
            for status in order_result["response"]["data"]["statuses"]:
                try:
                    filled = status["filled"]
                    print(
                        f'Hyperliquid Order #{filled["oid"]} filled {filled["totalSz"]} @{filled["avgPx"]}'
                    )
                except KeyError:
                    print(f'Error: {status["error"]}')
                    return order_result["status"]

        return order_result

    def create_limit_order(
        self,
        symbol: str,
        sz_usd: float,
        side: Side,
        limit_px: float,
    ):
        """
        Create a limit order.

        Parameters:
        symbol (str): The symbol/ticker of the asset to trade.
        sz_usd (float): The size of the order in USD.
        side (Side): The side of the trade (BUY or SELL).
        limit_px (float): The limit price.

        Returns:
        dict: The response from the Hyperliquid API after placing the order.
        """
        px = limit_px
        sz = sz_usd / px

        order_spec = hyperliq_utils.get_limit_open_order(
            symbol,
            sz,
            side.value,
            limit_px,
        )
        hyperliq_utils.check_order_leverage(
            self.info.user_state(self.address), symbol, sz, side.value
        )
        order_result = self.exchange.order(order_spec, {"gasPrice": "0"})

        if order_result["status"] == "ok":
            for status in order_result["response"]["data"]["statuses"]:
                try:
                    print(
                        f'Hyperliquid Limit Order placed, status: {json.dumps(status)}'
                    )
                except KeyError:
                    print(f'Error: {status["error"]}')
                    return order_result["status"]

        return order_result

    def cancel_open_orders(self):
        """
        Cancel all open orders for the user on Hyperliquid.

        Returns:
        dict: The response from the Hyperliquid API after cancelling orders.
        """
        # Cancel all open orders
        cancel_response = self.exchange.cancel_all()
        try:
            print(
                f'Cancelled all open orders, status: {json.dumps(cancel_response["response"])}'
            )
        except Exception:
            print(f"Error cancelling orders: {cancel_response}")
        return cancel_response

    def market_close_an_asset(self, symbol: str):
        """
        Market close an asset if there is an open position.

        Parameters:
        symbol (str): The symbol of the asset to close.

        Returns:
        bool: True if the close was attempted, False if no position.
        """
        user_state = self.info.user_state(self.address)
        asset_positions = user_state["assetPositions"]
        asset_ctx = self.info.meta_and_asset_ctxs()[1]
        universe = asset_ctx["universe"]

        symbol_info = next((asset for asset in universe if asset["name"] == symbol), None)
        if symbol_info is None:
            raise ValueError(f"Symbol '{symbol}' not found in the Hyperliquid universe.")

        coin_index = symbol_info["index"]

        position = next(
            (
                position
                for position in asset_positions
                if position["position"]["coin"] == coin_index
            ),
            None,
        )

        if position:
            size = float(position["position"]["szi"])
            side = Side.SELL if size > 0 else Side.BUY
            print(
                f"Closing {symbol} position of size {size} on Hyperliquid, side: {side.value}"
            )
            self.create_market_order(symbol, abs(size) * float(symbol_info["markPx"]), side)
            return True

        print(f"No open position for {symbol} on Hyperliquid")
        return False

    def get_open_positions(self):
        """
        Get a list of open positions with non-zero size.

        Returns:
        list[dict]: List of positions: [{"symbol": str, "position_size": float}, ...]
        """
        # Get the user state and print out position information
        user_state = self.info.user_state(self.address)
        filtered_positions = []
        for position in user_state["assetPositions"]:
            symbol = position["position"]["coin"]
            position_size = float(position["position"]["szi"])
            if position_size != 0:
                filtered_positions.append(
                    {"symbol": symbol, "position_size": position_size}
                )

        if len(filtered_positions) == 0:
            print("     No open positions")

        return filtered_positions
