import json
import os
import sys
import time

import requests
from base58 import b58decode
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dotenv import load_dotenv
from eth_account import Account
from prompt_toolkit import HTML, print_formatted_text


# Global network mode: 0=testnet (default), 1=mainnet
RUN_MAINNET = int(os.getenv("RUN_MAINNET", "0"))
IS_MAINNET = RUN_MAINNET == 1
# Explicit environment label for Orderly public REST calls
ORDERLY_ENV = "mainnet" if IS_MAINNET else "testnet"
# Base host for Orderly EVM public/private REST calls
ORDERLY_BASE_URL = (
    "https://api-evm.orderly.org" if IS_MAINNET else "https://testnet-api-evm.orderly.org"
)

# Internal module paths
sys.path.append("src")
sys.path.append("src/orderly")
sys.path.append("src/hyperliq")
sys.path.append("src/backpack")

from hyperliq.hyperliq_utils import hyperliquid_setup
from hyperliq.funding_rate import HyperliquidFundingRates
from hyperliq.order import HyperLiquidOrder
from hyperliquid.utils import constants

from orderly.funding_rate import OrderlyFundingRates
from orderly.client import Client
from orderly.config import Config
from orderly.order import Side
from orderly.util import print_ascii_art
from backpack.funding_rate import BackpackFundingRates

from strategies.funding_rate_arbitrage import FundingRateArbitrage

load_dotenv()


def prompt_user(options, prompt: str) -> int:
    """Simple text menu — returns the chosen option number (1..n)."""
    if prompt:
        print(prompt)
    for i, option in enumerate(options, 1):
        print(f"{i}) {option} ")
    try:
        choice = int(input("Enter your choice: "))
    except ValueError:
        return -1
    return choice


def clear_screen():
    """Clear the terminal screen for the CLI."""
    os.system("cls" if os.name == "nt" else "clear")


def get_dex_from_dex_options(choice: int):
    try:
        return dex_options[choice - 1]
    except IndexError:
        print("Invalid choice, aborting!")
        return None


def analyze_funding_rate_arbitrage(option: int):
    """Funding-rate arbitrage analyzer.

    option:
      1 -> show all rates
      2 -> top-3 deltas across all DEXes
    """

    # Pull fresh funding rates from every DEX on each call
    dex_rates_list = [
        (
            "orderly",
            OrderlyFundingRates(env=ORDERLY_ENV).get_orderly_funding_rates(),
        ),
        (
            "hyperliquid",
            HyperliquidFundingRates(
                hl_address,
                hl_info,
                hl_exchange,
            ).get_hyperliquid_funding_rates(),
        ),
        (
            "backpack",
            BackpackFundingRates().get_backpack_funding_rates(),
        ),
        # *** ADD NEW DEX HERE ***:
    ]

    # Initialise the strategy
    fr_arbitrage = FundingRateArbitrage()

    # Push each venue's rates into the analyzer
    for dex_name, rates in dex_rates_list:
        fr_arbitrage.add_dex_rates(dex_name, rates)

    # Build the unified table
    compiled_rates = fr_arbitrage.compile_rates()
    df = fr_arbitrage.create_rates_table(compiled_rates)

    if option == 1:
        fr_arbitrage.display_rates_table(df)
    elif option == 2:
        fr_arbitrage.display_top_rates_differences_from_all_DEXs(df)


def market_close_an_asset(dex: str, symbol: str):
    """Market-close a position by symbol on the chosen DEX."""
    if dex == "orderly":
        response = client.order.market_close_an_asset(symbol)
        success = response.get("success") is True
        if not success:
            print(response.get("message"))
        return success

    elif dex == "hyperliquid":
        success = hyperliquid_order.market_close_an_asset(symbol)
        return success

    # * elif ADD NEW DEX HERE

    print(f"Unsupported DEX: {dex}")
    return False


def create_order(dex: str, symbol: str, quantity: float, side: Side):
    """Create a market order on any supported DEX."""
    if dex == "orderly":
        order_result = client.order.create_market_order(symbol, quantity, side)
        success = order_result.get("success") is True

        # Fetch mark price from the public API (respects network mode)
        url = (
            f"{ORDERLY_BASE_URL}/v1/public/futures/"
            f"PERP_{symbol}_USDC"
        )
        response = json.loads(requests.request("GET", url).text)

        if success:
            print_formatted_text(
                f"Orderly order #{order_result['data']['order_id']} ",
                "filled ",
                HTML(
                    f"<ansigreen>{order_result['data']['order_quantity']}</ansigreen>"
                ),
                " at ",
                HTML(f"<ansigreen>{response['data']['mark_price']}</ansigreen>"),
            )
        return success

    elif dex == "hyperliquid":
        order_result = hyperliquid_order.create_market_order(symbol, quantity, side)
        success = order_result.get("status") == "ok"
        if success:
            for status in order_result["response"]["data"]["statuses"]:
                try:
                    filled = status["filled"]
                    print_formatted_text(
                        f"Hyperliquid order #{filled['oid']} ",
                        "filled ",
                        HTML(f"<ansigreen>{filled['totalSz']}</ansigreen>"),
                        " at ",
                        HTML(f"<ansigreen>{filled['avgPx']}</ansigreen>"),
                    )
                except KeyError:
                    print(f"Error: {status.get('error')}")
                    return False
        return success

    # * elif ADD NEW DEX HERE

    print(f"Unsupported DEX: {dex}")
    return False


# Hard ceiling on a single delta-neutral leg, in USD-equivalent units.
# Adjust deliberately; safety net against fat-finger / runaway orders.
MAX_ORDER_SIZE_USD = float(os.getenv("MAX_ORDER_SIZE_USD", "1000"))


def execute_funding_rate_arbitrage(
    symbol: str, short_on_dex: str, long_on_dex: str, order_quantity: float
) -> bool:
    """Open a delta-neutral pair: short the high-funding venue, long the low-funding venue.

    Enforces a global ``MAX_ORDER_SIZE_USD`` cap so a stray prompt cannot ship a
    nine-figure order. If the cap is exceeded the strategy refuses to run.
    """

    # Defensive size cap — refuse rather than silently clamp so caller sees the issue.
    if order_quantity <= 0:
        print(f"Refusing to execute: order_quantity must be positive (got {order_quantity}).")
        return False
    if order_quantity > MAX_ORDER_SIZE_USD:
        print(
            f"Refusing to execute: order_quantity {order_quantity} exceeds "
            f"MAX_ORDER_SIZE_USD={MAX_ORDER_SIZE_USD}. Raise the env var deliberately if intended."
        )
        return False

    # Short leg first — if it fails we never enter the long leg.
    if not create_order(short_on_dex, symbol, order_quantity, Side.SELL):
        print(f"{short_on_dex.title()} order failed, aborting strategy")
        return False

    # Long leg
    if not create_order(long_on_dex, symbol, order_quantity, Side.BUY):
        print(f"{long_on_dex.title()} order failed, aborting strategy")
        print("Close the short position manually or via menu!")
        return False

    return True


def print_open_positions(dex: str):
    """Print open positions for the chosen DEX."""
    if dex == "orderly":
        print("Orderly Positions:")
        positions = client.order.get_all_positions()
    elif dex == "hyperliquid":
        print("Hyperliquid Positions:")
        positions = hyperliquid_order.get_open_positions()
    else:
        positions = []

    for position in positions:
        symbol = position["symbol"]
        size = float(position["position_size"])
        if size > 0:
            print_formatted_text(
                f"     {symbol}: ", HTML(f"<ansigreen>{size}</ansigreen>")
            )
        else:
            print_formatted_text(
                f"     {symbol}: ", HTML(f"<ansired>{size}</ansired>")
            )


def cancel_open_orders(dex: str):
    """Cancel all open orders on the chosen DEX."""
    if dex == "orderly":
        client.order.cancel_all_orders()
    elif dex == "hyperliquid":
        hyperliquid_order.cancel_open_orders()
    # * elif ADD NEW DEX HERE


def print_available_USDC_per_DEX(label: str, amount: float):
    """Pretty-print a USDC balance line."""
    if amount >= 1_000_000:
        formatted_amount = f"{amount:,.0f}"
    elif amount >= 100_000:
        formatted_amount = f"{amount:,.1f}"
    else:
        formatted_amount = f"{amount:,.2f}"

    print_formatted_text(
        label,
        HTML(f" <ansigreen>{formatted_amount} USDC available</ansigreen>"),
    )


if __name__ == "__main__":

    print(
        f"Network mode: {'MAINNET' if IS_MAINNET else 'TESTNET'} "
        f"(set RUN_MAINNET=1 in .env to switch to mainnet)."
    )
    if not IS_MAINNET:
        print("Ensure you are using testnet accounts and funds.")
    else:
        print("WARNING: live money will be at risk on every order. Review .env carefully.")

    address = os.getenv("WALLET_ADDRESS")
    print("Running with account address:", address)

    # ---------- ORDERLY SETUP ----------

    # PRIVATE_KEY — EVM key (0x...) used for Orderly EIP-712 onboarding signing.
    account: Account = Account.from_key(os.getenv("PRIVATE_KEY"))

    if IS_MAINNET:
        orderly_base_url = os.getenv(
            "ORDERLY_BASE_URL_MAINNET",
            "https://api.orderly.org",
        )
        orderly_chain_id = int(os.getenv("ORDERLY_CHAIN_ID_MAINNET", "42161"))
        orderly_secret_env = "ORDERLY_SECRET_MAINNET"
    else:
        orderly_base_url = os.getenv(
            "ORDERLY_BASE_URL_TESTNET",
            "https://testnet-api-evm.orderly.org",
        )
        orderly_chain_id = int(os.getenv("ORDERLY_CHAIN_ID_TESTNET", "421614"))
        orderly_secret_env = "ORDERLY_SECRET_TESTNET"

    config = Config(base_url=orderly_base_url, chain_id=orderly_chain_id)
    client = Client(config, account)

    # Orderly Ed25519 key (base58) — pulled from the env var that matches the active network.
    key_b58 = os.getenv(orderly_secret_env)
    if not key_b58:
        raise RuntimeError(f"Missing {orderly_secret_env} in environment")

    key_bytes = b58decode(key_b58)
    orderly_key = Ed25519PrivateKey.from_private_bytes(key_bytes)
    client.signer._key_pair = orderly_key
    print(f"Connected to Orderly ({'mainnet' if IS_MAINNET else 'testnet'})")

    # ---------- HYPERLIQUID SETUP ----------

    hl_base_url = constants.MAINNET_API_URL if IS_MAINNET else constants.TESTNET_API_URL
    hl_address, hl_info, hl_exchange = hyperliquid_setup(
        hl_base_url, skip_ws=True
    )
    hyperliquid_order = HyperLiquidOrder(hl_address, hl_info, hl_exchange)
    print(f"Connected to Hyperliquid ({'mainnet' if IS_MAINNET else 'testnet'})")

    # ---------- MAIN LOOP ----------

    while True:
        clear_screen()
        print_ascii_art()

        # Available DEXes
        dex_options = [
            "orderly",
            "hyperliquid",
            # *** ADD NEW DEX HERE ***
        ]

        main_options = [
            "View USDC balances on each DEX",  # 1
            "View open positions",             # 2
            "Close positions",                 # 3
            "Cancel open orders",              # 4
            "Funding rate arbitrage tools",    # 5
            "Exit",                            # 6
        ]
        choice = prompt_user(main_options, "What would you like to do?\n")

        # ----- 1. Balances -----
        if choice == 1:
            print("\n")

            # Orderly: defensively handle the empty-holdings case
            try:
                holdings = client.account.get_client_holding()
            except Exception:
                holdings = []

            if holdings and len(holdings) > 0 and "holding" in holdings[0]:
                orderly_amount = float(holdings[0]["holding"])
            else:
                orderly_amount = 0.0

            print_available_USDC_per_DEX("Orderly balance", orderly_amount)

            # Hyperliquid: take the withdrawable balance when the field is present
            try:
                user_state = hl_info.user_state(hl_address)
                hyperliquid_amount = float(user_state.get("withdrawable", 0.0))
            except Exception:
                hyperliquid_amount = 0.0

            print_available_USDC_per_DEX(
                "Hyperliquid balance", hyperliquid_amount
            )

            back_options = ["Back to Main Menu"]
            _ = prompt_user(back_options, "")
            continue

        # ----- 2. Open positions -----
        elif choice == 2:
            print("\n")
            for dex in dex_options:
                print_open_positions(dex)
                print("\n")

            back_options = ["Back to Main Menu"]
            _ = prompt_user(back_options, "")
            continue

        # ----- 3. Close positions -----
        elif choice == 3:
            dex_choice = prompt_user(
                dex_options, "\nWhat DEX would you like to close positions on?"
            )
            close_on_dex = get_dex_from_dex_options(dex_choice)
            if not close_on_dex:
                time.sleep(1.5)
                continue

            print("\nWhen entering a symbol, just enter the symbol itself i.e. ETH\n")
            symbol = input("Symbol to close: ").upper()

            success = market_close_an_asset(close_on_dex, symbol)
            if success:
                print_formatted_text(
                    HTML(f"<ansigreen>{symbol}</ansigreen>"),
                    " has been closed on ",
                    HTML(f"<ansigreen>{close_on_dex}</ansigreen>"),
                )
            else:
                print(
                    f"Error closing {symbol} position on {close_on_dex}. "
                    f"Manual intervention may be required."
                )

            back_options = ["Back to Main Menu"]
            _ = prompt_user(back_options, "")
            continue

        # ----- 4. Cancel open orders -----
        elif choice == 4:
            while True:
                local_dex_options = dex_options.copy()
                local_dex_options.append("Back to Main Menu")

                dex_choice = prompt_user(
                    local_dex_options,
                    "\nWhat DEX would you like to cancel open orders on?",
                )

                # Back
                if dex_choice == len(local_dex_options):
                    break

                dex = get_dex_from_dex_options(dex_choice)
                if not dex:
                    time.sleep(1.5)
                    break

                cancel_open_orders(dex)
                print_formatted_text(
                    "Open orders have been cancelled on ",
                    HTML(f"<ansigreen>{dex}</ansigreen>"),
                )

            continue

        # ----- 5. Funding Rate Arbitrage Tools -----
        elif choice == 5:
            while True:
                fr_options = [
                    "View rates on all available DEXs",
                    "View top 3 rate differences from all DEXs",
                    "Execute Strategy",
                    "Back to Main Menu",
                ]
                sub_choice = prompt_user(fr_options, "\nWhat would you like to do?")

                # View all rates
                if sub_choice == 1:
                    clear_screen()
                    analyze_funding_rate_arbitrage(1)

                # Top-3 among all DEXs
                elif sub_choice == 2:
                    clear_screen()
                    analyze_funding_rate_arbitrage(2)

                # Execute Strategy
                elif sub_choice == 3:
                    clear_screen()
                    print(
                        "\nWhen entering a symbol, just enter the symbol itself i.e. ETH\n"
                    )
                    symbol = input("Symbol: ").upper()

                    local_dex_options = dex_options.copy()

                    short_choice = prompt_user(
                        local_dex_options, "What DEX would you like to short on?"
                    )
                    short_on_dex = get_dex_from_dex_options(short_choice)
                    if not short_on_dex:
                        print("Aborting!")
                        time.sleep(2)
                        break

                    remaining_dex = [
                        d for d in local_dex_options if d != short_on_dex
                    ]

                    long_choice = prompt_user(
                        remaining_dex, "What DEX would you like to long on?"
                    )
                    long_on_dex = (
                        remaining_dex[long_choice - 1]
                        if 1 <= long_choice <= len(remaining_dex)
                        else None
                    )
                    if not long_on_dex:
                        print("Aborting!")
                        time.sleep(2)
                        break

                    try:
                        order_quantity = float(input("\nEnter Order Quantity: "))
                    except ValueError:
                        print("Invalid number, aborting!")
                        time.sleep(2)
                        break

                    print("\nYou chose to:")
                    print_formatted_text(
                        "Short on DEX: ",
                        HTML(f"<ansired>{short_on_dex}</ansired>"),
                    )
                    print_formatted_text(
                        "Long on DEX: ",
                        HTML(f"<ansigreen>{long_on_dex}</ansigreen>"),
                    )
                    print(f"Order Quantity: {order_quantity}")

                    confirm_options = ["Yes", "No"]
                    confirm_choice = prompt_user(
                        confirm_options, "Are you sure this is correct?"
                    )
                    if confirm_choice == 1:
                        print("Okay! Let's arbitrage!")
                    elif confirm_choice == 2:
                        print("Aborting!")
                        time.sleep(2)
                        break
                    else:
                        print("Invalid choice, aborting!")
                        time.sleep(2)
                        break

                    if execute_funding_rate_arbitrage(
                        symbol, short_on_dex, long_on_dex, order_quantity
                    ):
                        print_formatted_text(
                            HTML(
                                "<ansiblue>\nCongrats!🥳 You have "
                                "successfully performed the Funding "
                                "Rate Arbitrage!</ansiblue>"
                            )
                        )

                elif sub_choice == 4:
                    break
                else:
                    print("\nInvalid choice, please try again!")
                    time.sleep(1.5)

            continue

        # ----- 6. Exit -----
        elif choice == 6:
            print("\nExiting program, have a good day 😊\n")
            break

        else:
            print("\nInvalid choice, please try again")
            time.sleep(1.5)
