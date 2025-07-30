import yfinance as yf
import pandas as pd
from basetool import BaseTool
from typing import List, Dict, Any, Union

class StockDataTool(BaseTool):
    """
    A tool for fetching historical stock data.
    """

    @property
    def name(self) -> str:
        return "stock_data_fetcher"

    @property
    def description(self) -> str:
        return "Fetches historical stock data for a given ticker using the yfinance library."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "ticker", "type": "string", "description": "The stock ticker symbol."},
            {"name": "time_range", "type": "string", "description": "The time range for the data (e.g., '1mo', '1y')."}
        ]

    def execute(self, ticker: str, time_range: str = '1mo') -> Union[List[Dict[str, Any]], Dict[str, str]]:
        """
        Fetches historical stock data for a given ticker using the yfinance library.
        """
        print(f"[yfinance] Fetching data for ticker: {ticker}, range: {time_range}")
        try:
            stock = yf.Ticker(ticker)
            
            period_map = {
                '1d': '1d', '5d': '5d', '1wk': '1wk', '1mo': '1mo',
                '3mo': '3mo', '6mo': '6mo', 'ytd': 'ytd', '1y': '1y',
                '5y': '5y', 'max': 'max'
            }
            period = period_map.get(time_range, '1mo')
            
            interval = '1h' if period == '1d' else '1d'

            hist = stock.history(period=period, interval=interval)

            if hist.empty:
                if '-' not in ticker:
                     print(f"[yfinance] No data for {ticker}, trying {ticker}-USD")
                     hist = yf.Ticker(f"{ticker}-USD").history(period=period, interval=interval)
                if hist.empty:
                    print(f"[yfinance] No data found for ticker: {ticker} (or fallback)")
                    return {"error": f"No historical data found for ticker '{ticker}'. It might be delisted or an invalid symbol."}

            hist = hist.reset_index()
            
            date_col_name = next((col for col in ['Datetime', 'Date'] if col in hist.columns), None)
            if not date_col_name:
                raise ValueError("Date or Datetime column not found in yfinance history.")

            hist.rename(columns={
                date_col_name: 'date', 'Open': 'open', 'High': 'high',
                'Low': 'low', 'Close': 'close', 'Volume': 'volume'
            }, inplace=True)

            hist['date'] = pd.to_datetime(hist['date']).dt.strftime('%Y-%m-%d %H:%M:%S')

            required_cols = ['date', 'open', 'high', 'low', 'close', 'volume']
            result_df = hist[[col for col in required_cols if col in hist.columns]]

            data = result_df.to_dict('records')
            print(f"[yfinance] Successfully fetched {len(data)} data points for {ticker}.")
            return data

        except Exception as e:
            print(f"[yfinance] Error fetching data for {ticker}: {e}")
            return {"error": f"An error occurred while fetching data for '{ticker}': {str(e)}"}