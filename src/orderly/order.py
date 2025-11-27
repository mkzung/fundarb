try:
    # Python 3.11+
    from enum import StrEnum
except ImportError:
    # Backport для Python < 3.11
    from enum import Enum

    class StrEnum(str, Enum):
        """Backport of StrEnum for Python < 3.11."""
        pass

from eth_account import Account
import json
from requests import Request, Session
from config import Config
from signer import Signer
from util import get_orderly_naming_convention


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class Order(object):
    def __init__(
        self,
        config: Config,
        session: Session,
        signer: Signer,
        account: Account,
    ) -> None:
        self._config = config
        self._session = session
        self._signer = signer
        self._account = account

    def _send_request(self, request: Request):
        """Helper function: подписать запрос и отправить его."""
        req = self._signer.sign_request(request)
        res = self._session.send(req)
        response = json.loads(res.text)
        return response

    # ---------- BASIC ORDER METHODS ----------

    def get_orders(self):
        """Получить список ордеров."""
        request = Request("GET", f"{self._config.base_url}/v1/orders")
        return self._send_request(request)

    def create_market_order(
        self,
        symbol: str,
        order_quantity: float,
        side: Side,
    ):
        """
        Создать маркет-ордер на Orderly.

        :param symbol: тикер (например, 'ETH')
        :param order_quantity: количество (в контрактных единицах)
        :param side: Side.BUY или Side.SELL
        """
        symbol = get_orderly_naming_convention(symbol)

        request = Request(
            "POST",
            f"{self._config.base_url}/v1/order",
            json={
                "symbol": symbol,
                "order_type": str(OrderType.MARKET),
                "order_quantity": order_quantity,
                "side": str(side),
            },
        )
        return self._send_request(request)

    # TODO: Create limit order
    def create_limit_order(
        self,
        symbol: str,
        order_quantity: float,
        side: Side,
    ):
        """
        Заглушка под лимитный ордер (пока не реализован).
        """
        raise NotImplementedError("Limit orders are not implemented yet.")

    # ---------- POSITION / MANAGEMENT METHODS ----------

    def market_close_an_asset(self, symbol: str):
        """
        Маркет-закрытие позиции по указанному символу.

        :param symbol: тикер (например, 'ETH')
        """
        position_data = self.get_position(symbol)
        order_quantity = float(position_data["data"]["position_qty"])

        side = Side.BUY if order_quantity < 0 else Side.SELL

        if order_quantity != 0:
            return self.create_market_order(symbol, abs(order_quantity), side)
        else:
            print("No position held in this symbol")

    def cancel_all_orders(self):
        """
        Отменить все открытые ордера.
        Важно: endpoint /v1/orders (orders во множественном числе).
        """
        request = Request(
            "DELETE",
            f"{self._config.base_url}/v1/orders",
            # Be careful, orders has to be plural here
        )
        return self._send_request(request)

    def get_position(self, symbol: str):
        """
        Получить позицию по конкретному символу.
        """
        symbol = get_orderly_naming_convention(symbol)
        request = Request(
            "GET",
            f"https://testnet-api-evm.orderly.network/v1/position/{symbol}",
        )
        return self._send_request(request)

    def get_all_positions(self) -> list:
        """
        Получить все открытые позиции на Orderly.

        :return: список словарей вида
                 [{"symbol": "ETH", "position_size": 0.5}, ...]
        """
        request = Request(
            "GET",
            "https://testnet-api-evm.orderly.network/v1/positions",
        )
        positions_data = self._send_request(request)
        filtered_positions = []

        for position in positions_data["data"]["rows"]:
            # Конвертим формат Orderly (PERP_XXX_USDC) в обычный тикер
            symbol = position["symbol"].replace("PERP_", "").replace("_USDC", "")
            position_size = position["position_qty"]

            if position_size != 0:
                filtered_positions.append(
                    {
                        "symbol": symbol,
                        "position_size": position_size,
                    }
                )

        if len(filtered_positions) == 0:
            print("     No open positions")

        return filtered_positions
