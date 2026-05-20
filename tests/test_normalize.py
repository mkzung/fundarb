"""Smoke tests for cross-venue symbol normalisation.

These tests do not require network access — they exercise the pure
normalisation logic so CI catches regressions on PRs.
"""
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from strategies.funding_rate_arbitrage import FundingRateArbitrage


def test_normalize_orderly_perp_prefix():
    assert FundingRateArbitrage._normalize_symbol("PERP_BTC_USDC") == "BTC"


def test_normalize_backpack_underscore():
    assert FundingRateArbitrage._normalize_symbol("BTC_USDC") == "BTC"


def test_normalize_hyperliquid_bare():
    assert FundingRateArbitrage._normalize_symbol("BTC") == "BTC"


def test_normalize_dash_perp():
    assert FundingRateArbitrage._normalize_symbol("BTC-USDC") == "BTC"


def test_normalize_lowercase():
    assert FundingRateArbitrage._normalize_symbol("btc_usdc") == "BTC"


def test_normalize_empty():
    assert FundingRateArbitrage._normalize_symbol("") == ""


def test_cross_venue_collision():
    """All three venue representations of BTC collapse to the same key."""
    syms = ["BTC", "PERP_BTC_USDC", "BTC_USDC"]
    normalised = {FundingRateArbitrage._normalize_symbol(s) for s in syms}
    assert normalised == {"BTC"}
