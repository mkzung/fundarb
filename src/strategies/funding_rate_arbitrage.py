import pandas as pd
from tabulate import tabulate


class FundingRateArbitrage:
    """Cross-venue funding-rate arbitrage analyzer.

    Accepts a `{symbol -> funding_rate}` mapping for each DEX and computes
    pairwise rate deltas plus arbitrage opportunities after costs.
    """

    def __init__(self) -> None:
        # { "orderly": {"BTC": 0.01, ...}, "hyperliquid": {...}, ... }
        self.dex_rates: dict[str, dict[str, float]] = {}

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """Normalize symbol names across DEXes.

        Removes common quote currency suffixes and PERP suffixes so that,
        for example, `BTC_USDC`, `PERP_BTC_USDC` and `BTC-PERP` all map to
        the same key `BTC`. Hyperliquid already returns bare symbols, so
        calling this on them is a no-op.
        """

        if not symbol:
            return symbol

        s = symbol.upper()

        # Remove known prefixes
        if s.startswith("PERP_"):
            s = s[len("PERP_") :]

        # Strip common quote currencies
        for suffix in ("_USDC", "_USDT", "-USDC", "-USDT"):
            if s.endswith(suffix):
                s = s[: -len(suffix)]
                break

        # Remove perpetual markers
        for suffix in ("-PERP", "_PERP"):
            if s.endswith(suffix):
                s = s[: -len(suffix)]
                break

        return s

    def add_dex_rates(self, dex_name: str, rates: dict | None) -> None:
        """Register funding rates for a single venue.

        :param dex_name: venue name (e.g. ``"orderly"`` or ``"hyperliquid"``)
        :param rates: ``{symbol: funding_rate}`` mapping
        """
        if not rates:
            return

        normalized = {}
        for symbol, rate in rates.items():
            norm_symbol = self._normalize_symbol(symbol)
            # Skip empty keys after normalization
            if not norm_symbol:
                continue
            normalized[norm_symbol] = rate

        if not normalized:
            return

        self.dex_rates[dex_name] = normalized

    def compile_rates(self) -> pd.DataFrame:
        """Combine every venue's rates into a single table.

        Rows are coins, columns are DEXes.

        :return: DataFrame with columns ``["orderly", "hyperliquid", ...]``
        """
        if not self.dex_rates:
            return pd.DataFrame()

        frames = []
        for dex, rates in self.dex_rates.items():
            # One Series per DEX, named after the DEX.
            s = pd.Series(rates, name=dex)
            frames.append(s)

        df = pd.concat(frames, axis=1)
        return df

    def create_rates_table(self, df: pd.DataFrame) -> pd.DataFrame:
        """Annotate the rates DataFrame with summary columns.

        Adds:
        - ``MaxRate``: max rate per coin across DEXes
        - ``MinRate``: min rate
        - ``Difference``: ``MaxRate - MinRate``

        and promotes the index to a ``Symbol`` column.
        """
        if df is None or df.empty:
            return pd.DataFrame()

        df = df.copy()
        # Aggregate across every DEX column (all numeric).
        df["MaxRate"] = df.max(axis=1, skipna=True)
        df["MinRate"] = df.min(axis=1, skipna=True)
        df["Difference"] = df["MaxRate"] - df["MinRate"]

        df.index.name = "Symbol"
        df.reset_index(inplace=True)

        dex_cols = self._dex_columns(df)
        if dex_cols:
            non_na_count = df[dex_cols].notna().sum(axis=1)
            df = df.loc[non_na_count >= 2].reset_index(drop=True)

        return df

    @staticmethod
    def _dex_columns(df: pd.DataFrame) -> list[str]:
        """Return only the columns that correspond to DEX rate series."""
        service_cols = {"Symbol", "MaxRate", "MinRate", "Difference"}
        return [c for c in df.columns if c not in service_cols]

    def display_rates_table(self, df: pd.DataFrame) -> None:
        """Print the full cross-venue funding-rate table."""
        if df is None or df.empty:
            print("No funding rate data available.")
            return

        display_df = self._format_display_df(df)
        print(
            tabulate(
                display_df,
                headers="keys",
                tablefmt="psql",
                showindex=False,
            )
        )

    def display_top_rates_differences_from_Orderly(
        self,
        df: pd.DataFrame,
        top_n: int = 3,
    ) -> None:
        """Print top-N coins by funding-rate spread that include Orderly.

        Filters:
        - Orderly rate must be non-NaN,
        - the coin must have a rate on at least 2 DEXes (so the comparison is meaningful),
        - ``Difference`` must be strictly non-zero.
        """
        if df is None or df.empty or "orderly" not in df.columns:
            print("No Orderly data available for comparison.")
            return

        dex_cols = self._dex_columns(df)
        if not dex_cols:
            print("No DEX columns found in the rates table.")
            return

        # How many DEXes actually quote a rate for each coin.
        non_na_count = df[dex_cols].notna().sum(axis=1)

        mask = (
            df["orderly"].notna()          # Orderly quote present
            & (non_na_count >= 2)          # at least one other DEX present
            & (df["Difference"].abs() > 0) # spread strictly non-zero
        )

        top = df[mask].sort_values("Difference", ascending=False).head(top_n)

        if top.empty:
            print(
                "No overlapping markets between Orderly and other DEXs "
                "with non-zero funding differences found."
            )
            return

        display_df = self._format_display_df(top)
        print(
            tabulate(
                display_df,
                headers="keys",
                tablefmt="psql",
                showindex=False,
            )
        )

    def display_top_rates_differences_from_all_DEXs(
        self,
        df: pd.DataFrame,
        top_n: int = 3,
    ) -> None:
        """Print top-N coins by spread across every DEX.

        Requires the coin to appear on at least 2 exchanges and have a
        non-zero ``Difference``.
        """
        if df is None or df.empty:
            print("No funding rate data available.")
            return

        dex_cols = self._dex_columns(df)
        if not dex_cols:
            print("No DEX columns found in the rates table.")
            return

        non_na_count = df[dex_cols].notna().sum(axis=1)

        mask = (non_na_count >= 2) & (df["Difference"].abs() > 0)

        top = df[mask].sort_values("Difference", ascending=False).head(top_n)

        if top.empty:
            print(
                "No overlapping markets with non-zero funding differences "
                "found across DEXs."
            )
            return

        display_df = self._format_display_df(top)
        print(
            tabulate(
                display_df,
                headers="keys",
                tablefmt="psql",
                showindex=False,
            )
        )

    @staticmethod
    def _format_display_df(df: pd.DataFrame) -> pd.DataFrame:
        """Format funding-rate output for readable CLI display.

        * Replace missing values with a dash instead of NaN/placeholder numbers.
        * Format numeric values to 6 decimal places for consistent columns.
        """

        display_df = df.copy()
        for col in display_df.columns:
            if col == "Symbol":
                continue
            display_df[col] = display_df[col].apply(
                lambda v: "-" if pd.isna(v) else f"{float(v):.6f}"
            )

        return display_df
