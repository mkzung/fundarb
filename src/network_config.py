"""Shared runtime configuration.

Single source of truth for the network-mode flag so library modules don't
have to import from ``main`` (which would cause circular imports). Values
come from environment variables loaded by python-dotenv in ``main.py`` or
by individual modules that call ``load_dotenv()`` themselves.
"""

import os

from dotenv import load_dotenv

load_dotenv()


# Network mode: 0 = testnet (default), 1 = mainnet.
RUN_MAINNET = int(os.getenv("RUN_MAINNET", "0"))
IS_MAINNET: bool = RUN_MAINNET == 1

# Orderly EVM public/private REST base URL — switch by network.
ORDERLY_BASE_URL = (
    "https://api-evm.orderly.org" if IS_MAINNET else "https://testnet-api-evm.orderly.org"
)
ORDERLY_ENV = "mainnet" if IS_MAINNET else "testnet"
