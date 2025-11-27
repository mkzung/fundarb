import pandas as pd
from tabulate import tabulate


class FundingRateArbitrage:
    """
    Класс для анализа арбитража по funding rate между несколькими DEX.
    Ожидает, что для каждого DEX мы передаём словарь {symbol -> funding_rate}.
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
        """
        Добавить ставки funding для одной биржи.

        :param dex_name: имя DEX (например, "orderly" или "hyperliquid")
        :param rates: словарь {symbol: funding_rate}
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
        """
        Собрать все ставки в одну таблицу:
        строки — монеты, колонки — DEX'ы.

        :return: DataFrame с колонками вида ["orderly", "hyperliquid", ...]
        """
        if not self.dex_rates:
            return pd.DataFrame()

        frames = []
        for dex, rates in self.dex_rates.items():
            # каждый DEX → Series, имя серии = имя DEX
            s = pd.Series(rates, name=dex)
            frames.append(s)

        df = pd.concat(frames, axis=1)
        return df

    def create_rates_table(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Добавляет к таблице:
        - MaxRate: максимальная ставка по монете среди DEX
        - MinRate: минимальная ставка
        - Difference: MaxRate - MinRate
        и превращает индекс в колонку Symbol.
        """
        if df is None or df.empty:
            return pd.DataFrame()

        df = df.copy()
        # По всем DEX-колонкам (они числовые)
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
        """Все колонки, которые относятся к DEX'ам (без служебных)."""
        service_cols = {"Symbol", "MaxRate", "MinRate", "Difference"}
        return [c for c in df.columns if c not in service_cols]

    def display_rates_table(self, df: pd.DataFrame) -> None:
        """
        Показать полную таблицу ставок по всем DEX.
        """
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
        """
        Показать топ-N монет с наибольшей разницей funding rate,
        где среди DEX обязательно есть 'orderly'.

        Фильтр:
        - есть ставка у Orderly (не NaN),
        - всего ставок по монете минимум с 2 DEX (чтобы был смысл сравнивать),
        - Difference != 0.
        """
        if df is None or df.empty or "orderly" not in df.columns:
            print("No Orderly data available for comparison.")
            return

        dex_cols = self._dex_columns(df)
        if not dex_cols:
            print("No DEX columns found in the rates table.")
            return

        # сколько DEX по каждой монете реально дают ставку
        non_na_count = df[dex_cols].notna().sum(axis=1)

        mask = (
            df["orderly"].notna()          # ставка на Orderly есть
            & (non_na_count >= 2)          # как минимум ещё один DEX
            & (df["Difference"].abs() > 0) # разница реально не нулевая
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
        """
        Топ-N монет с наибольшей разницей funding rate среди всех DEX,
        при условии, что монета есть минимум на 2 биржах и Difference != 0.
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
