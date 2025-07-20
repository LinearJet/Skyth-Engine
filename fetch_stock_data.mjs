// fetch_stock_data.mjs
// This script is called by app.py to fetch stock data using yahoo-finance2.
// It is not a server, but a command-line utility.
//
// Usage: node fetch_stock_data.mjs <TICKER> [range]
// Example: node fetch_stock_data.mjs AAPL 1mo
//
// On success, it prints a JSON array of data to stdout.
// On failure, it prints a JSON error object to stderr and exits with code 1.

import yahooFinance from 'yahoo-finance2';

// Suppress the deprecation notice
yahooFinance.suppressNotices(['ripHistorical']);

async function fetchData() {
  const ticker = process.argv[2];
  const range = process.argv[3] || '1mo'; // Default to 1 month

  if (!ticker) {
    console.error(JSON.stringify({ error: 'No ticker symbol provided to the script.' }));
    process.exit(1);
  }

  const endDate = new Date();
  let startDate = new Date();
  let interval = '1d';

  // Calculate date ranges based on the requested period
  switch (range) {
    case '1d':
      startDate.setDate(endDate.getDate() - 1);
      interval = '1m';
      break;
    case '5d':
      startDate.setDate(endDate.getDate() - 5);
      interval = '5m';
      break;
    case '1wk':
      startDate.setDate(endDate.getDate() - 7);
      interval = '15m';
      break;
    case '1mo':
      startDate.setMonth(endDate.getMonth() - 1);
      interval = '1d';
      break;
    case '3mo':
      startDate.setMonth(endDate.getMonth() - 3);
      interval = '1d';
      break;
    case '6mo':
      startDate.setMonth(endDate.getMonth() - 6);
      interval = '1d';
      break;
    case 'ytd':
      startDate = new Date(endDate.getFullYear(), 0, 1);
      interval = '1d';
      break;
    case '1y':
      startDate.setFullYear(endDate.getFullYear() - 1);
      interval = '1d';
      break;
    case '2y':
      startDate.setFullYear(endDate.getFullYear() - 2);
      interval = '1wk';
      break;
    case '5y':
      startDate.setFullYear(endDate.getFullYear() - 5);
      interval = '1wk';
      break;
    case '10y':
      startDate.setFullYear(endDate.getFullYear() - 10);
      interval = '1mo';
      break;
    case 'max':
      startDate = new Date('1990-01-01');
      interval = '1mo';
      break;
    default:
      console.error(JSON.stringify({ error: `Invalid range specified: ${range}` }));
      process.exit(1);
  }

  // Ensure dates are valid
  if (isNaN(startDate.getTime()) || isNaN(endDate.getTime())) {
    console.error(JSON.stringify({ error: 'Invalid date calculation' }));
    process.exit(1);
  }

  try {
    console.error(`Fetching data for ${ticker} from ${startDate.toISOString()} to ${endDate.toISOString()} with interval ${interval}`);
    
    let result;
    
    // Try using the chart method with period1/period2 (Unix timestamps)
    try {
      const queryOptions = {
        period1: Math.floor(startDate.getTime() / 1000),
        period2: Math.floor(endDate.getTime() / 1000),
        interval: interval,
      };
      
      result = await yahooFinance.chart(ticker, queryOptions);
    } catch (chartError) {
      console.error(`Chart method failed, trying historical method: ${chartError.message}`);
      
      // Fallback to historical method if chart fails
      const queryOptions = {
        period1: startDate,
        period2: endDate,
        interval: interval,
      };
      
      result = await yahooFinance.historical(ticker, queryOptions);
      
      // Convert historical data format to chart format
      if (result && Array.isArray(result)) {
        result = {
          quotes: result.map(item => ({
            date: item.date,
            open: item.open,
            high: item.high,
            low: item.low,
            close: item.close,
            volume: item.volume
          }))
        };
      }
    }
    
    // Handle different response formats
    let quotes = [];
    if (result && result.quotes && Array.isArray(result.quotes)) {
      quotes = result.quotes;
    } else if (result && Array.isArray(result)) {
      quotes = result;
    } else {
      throw new Error(`Unexpected response format from API for ticker '${ticker}'.`);
    }
    
    if (quotes.length === 0) {
      throw new Error(`No data returned from API. The ticker '${ticker}' may be invalid or delisted.`);
    }

    // Format the data into a clean structure for Python to consume
    const formattedData = quotes.map(quote => ({
      date: quote.date instanceof Date ? quote.date.toISOString() : 
            (typeof quote.date === 'string' ? new Date(quote.date).toISOString() : 
             new Date().toISOString()),
      close: quote.close || null,
      open: quote.open || null,
      high: quote.high || null,
      low: quote.low || null,
      volume: quote.volume || null
    })).filter(item => item.close !== null && !isNaN(item.close)); // Filter out invalid entries

    // Sort by date to ensure chronological order
    formattedData.sort((a, b) => new Date(a.date) - new Date(b.date));

    if (formattedData.length === 0) {
      throw new Error(`No valid price data found for ticker '${ticker}'.`);
    }

    console.error(`Successfully fetched ${formattedData.length} data points`);
    
    // Print the final JSON to standard output
    console.log(JSON.stringify(formattedData));

  } catch (error) {
    // Enhanced error handling
    let errorMessage = error.message || 'Unknown error occurred';
    
    // Handle specific Yahoo Finance errors
    if (errorMessage.includes('Invalid options') || errorMessage.includes('Validation called with invalid options')) {
      errorMessage = `Invalid query parameters for ticker '${ticker}'. The ticker may be invalid or the requested time range may not be supported.`;
    } else if (errorMessage.includes('Not found') || errorMessage.includes('404')) {
      errorMessage = `Ticker '${ticker}' not found. Please verify the ticker symbol.`;
    } else if (errorMessage.includes('Too Many Requests') || errorMessage.includes('429')) {
      errorMessage = `Rate limit exceeded. Please try again in a few minutes.`;
    } else if (errorMessage.includes('Unauthorized') || errorMessage.includes('401')) {
      errorMessage = `Unauthorized access. The API may be temporarily unavailable.`;
    } else if (errorMessage.includes('timeout')) {
      errorMessage = `Request timeout. Please try again.`;
    } else if (errorMessage.includes('ENOTFOUND') || errorMessage.includes('network')) {
      errorMessage = `Network error. Please check your internet connection.`;
    }
    
    console.error(JSON.stringify({ 
      error: errorMessage,
      ticker: ticker,
      range: range,
      interval: interval,
      timestamp: new Date().toISOString(),
      originalError: error.message
    }));
    process.exit(1);
  }
}

// Add error handling for uncaught exceptions
process.on('uncaughtException', (error) => {
  console.error(JSON.stringify({ 
    error: `Uncaught exception: ${error.message}`,
    stack: error.stack 
  }));
  process.exit(1);
});

process.on('unhandledRejection', (reason, promise) => {
  console.error(JSON.stringify({ 
    error: `Unhandled promise rejection: ${reason}`,
    promise: promise.toString()
  }));
  process.exit(1);
});

fetchData();