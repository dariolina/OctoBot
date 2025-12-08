# External Signal Strategy for OctoBot - Spot Trading

This module provides integration with external AI trading signals, allowing OctoBot to execute **spot trades** based on signals from your custom AI backend.

## Overview

The External Signal Strategy consists of three main components:

1. **Signal Client** (`octobot/utils/signal_client.py`): Fetches and validates signals from your REST API
2. **Strategy Evaluator** (`strategy_evaluator.py`): Polls for signals and evaluates trading opportunities
3. **Trading Mode** (`trading_mode.py`): Executes spot trades with stop-loss and time-based exits

## Features

- Fetches signals from a configurable REST endpoint
- Supports buy, sell, and no-trade signals
- Automatic signal freshness validation (configurable, default 10 minutes)
- Built-in caching with fallback on network failures
- Configurable bias (confidence) thresholds
- Automatic 1% stop-loss order placement
- Time-based position closure at signal's `close_time`
- **Spot trading only** (no leverage, no shorting)

## Installation

### 1. Add Configuration

Add the following section to your OctoBot configuration file (usually `user/config.json` or `config.json`):

```json
{
  "external_signal": {
    "enabled": true,
    "url": "http://localhost:8000/latest",
    "freshness_seconds": 600,
    "timeout": 10,
    "poll_interval_seconds": 60
  }
}
```

### 2. Configure Trading Mode Settings (Optional)

If using as a tentacle, you can configure these settings in the trading mode configuration:

```json
{
  "position_size_percent": 10,
  "min_bias": 50.0,
  "stop_loss_percent": 1.0
}
```

## Configuration Parameters

### external_signal

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enabled` | boolean | false | Enable external signal strategy |
| `url` | string | - | REST endpoint URL to fetch signals from |
| `freshness_seconds` | number | 600 | Maximum age of signal in seconds (10 minutes) |
| `timeout` | number | 10 | HTTP request timeout in seconds |
| `poll_interval_seconds` | number | 60 | Interval between signal fetches |

### Trading Mode Settings

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `position_size_percent` | number | 10 | Percentage of portfolio to use per trade |
| `min_bias` | number | 50.0 | Minimum signal bias (0.0 to 100.0) |
| `stop_loss_percent` | number | 1.0 | Stop loss percentage (e.g., 1.0 = 1%) |

## Signal Format

Your REST API endpoint should return JSON in the following format:

```json
{
  "pair": "BTC-USDC",
  "timestamp": "2025-12-06T10:30:00Z",
  "action": "buy",
  "bias": 75.0,
  "close_time": "2025-12-06T14:30:00Z",
  "reason": "Strong bullish momentum detected"
}
```

**For complete signal format documentation, see `SIGNAL_FORMAT.md`**

### Signal Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `pair` | string | Yes | Trading pair (e.g., "BTC-USDC") |
| `timestamp` | string | Yes | ISO 8601 timestamp (UTC) |
| `action` | string | Yes | One of: "buy", "sell", "no-trade" |
| `bias` | number | Yes | Confidence percentage (0.0 to 100.0) |
| `close_time` | string | Yes | ISO 8601 timestamp for position close |
| `reason` | string | No | Human-readable reason for the signal |

### Action Values

- **`buy`**: Buy base currency with quote currency (e.g., buy BTC with USDC)
- **`sell`**: Sell all available base currency
- **`no-trade`**: No action (signal is ignored)

## How It Works

### 1. Signal Polling

The strategy evaluator polls your REST endpoint at the configured interval (default: every 60 seconds).

### 2. Signal Validation

Each signal is validated for:
- Required fields presence
- Valid action type ("buy", "sell", "no-trade")
- Numeric field validity (bias 0-100)
- Timestamp formats (ISO 8601)
- Signal freshness

### 3. Signal Processing

If a signal is valid and actionable:
- The signal is cached
- The trading mode is notified
- Orders are created with stop-loss

### 4. Trade Execution

For **buy** signals:
- Market buy order is created
- Stop-loss: `entry_price × (1 - stop_loss_percent/100)` (default: -1%)
- Position automatically closes at `close_time` OR when stop-loss triggers (whichever comes first)

For **sell** signals:
- Market sell order is created for all available base currency
- Any scheduled close tasks are cancelled

## Usage Examples

### Basic Usage

1. Start your signal backend server at `http://localhost:8000`
2. Configure OctoBot with the external_signal settings
3. Activate the ExternalSignalStrategyEvaluator in your tentacles configuration
4. Set ExternalSignalTradingMode as your trading mode
5. Start OctoBot

### Custom Signal Endpoint

```json
{
  "external_signal": {
    "enabled": true,
    "url": "https://api.example.com/signals/latest",
    "freshness_seconds": 300,
    "timeout": 15,
    "poll_interval_seconds": 30
  }
}
```

### Conservative Trading Settings

```json
{
  "position_size_percent": 5,
  "min_bias": 70.0,
  "stop_loss_percent": 1.0
}
```

## Logging

The module logs important events at various levels:

- **INFO**: Signal fetches, trade executions
- **WARNING**: Stale signals, network failures
- **ERROR**: Validation failures, execution errors
- **DEBUG**: Detailed polling information

Check your OctoBot logs for activity:

```
External signal strategy started. Polling every 60s from http://localhost:8000/latest
Fetched external signal: buy for BTC-USDC (bias: 75.0%)
Executing BUY trade for BTC/USDC
Buy order created: BTC/USDC qty=0.01 entry=50000.00 SL=49500.00
Scheduled position close for BTC/USDC at 2025-12-06T14:30:00Z
```

## Troubleshooting

### No trades being executed

1. Check that `external_signal.enabled` is `true`
2. Verify your signal endpoint is accessible
3. Check signal freshness (timestamp within freshness_seconds)
4. Verify bias meets min_bias threshold
5. Check logs for validation errors

### Network errors

- Increase `timeout` value
- Check firewall/network connectivity
- Verify URL is correct
- Check signal endpoint logs

### Invalid signal format

- Ensure all required fields are present
- Verify timestamp is ISO 8601 format
- Check action is one of: "buy", "sell", "no-trade"
- Ensure bias is 0-100
- Verify close_time is ISO 8601 format

### Orders not created

- Check exchange connectivity
- Verify sufficient balance
- Check minimum order size for the exchange
- Review trading mode activation

## API Integration Example

Example Python backend for serving signals:

```python
from fastapi import FastAPI
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel

app = FastAPI()

class Signal(BaseModel):
    pair: str
    timestamp: str
    action: str
    bias: float
    close_time: str
    reason: str = None

@app.get("/latest")
async def get_latest_signal():
    # Your AI logic here
    return Signal(
        pair="BTC-USDC",
        timestamp=datetime.now(timezone.utc).isoformat(),
        action="buy",
        bias=75.0,
        close_time=(datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
        reason="AI consensus: strong bullish signal"
    )
```

## Architecture

```
┌─────────────────────────┐
│  External Signal API    │
│  (Your AI Backend)      │
└───────────┬─────────────┘
            │ HTTP GET
            ▼
┌─────────────────────────┐
│  Signal Client          │
│  - Fetch & validate     │
│  - Cache signals        │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  Strategy Evaluator     │
│  - Poll signals         │
│  - Check freshness      │
│  - Evaluate confidence  │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  Trading Mode           │
│  - Calculate position   │
│  - Create buy order     │
│  - Set 1% stop-loss     │
│  - Schedule close_time  │
└─────────────────────────┘
```

## Advanced Usage

### Custom Signal Client

You can create a custom signal client for more advanced use cases:

```python
from octobot.utils.signal_client import ExternalSignalClient

client = ExternalSignalClient(
    url="https://api.example.com/signals",
    freshness_seconds=300,
    timeout=15
)

# Fetch signal for specific pair
signal = await client.get_signal(pair="BTC-USDC")

# Check if actionable
if client.is_signal_actionable(signal):
    print(f"Trade: {signal['action']} {signal['pair']}")
```

### Programmatic Access

Access signal data from other parts of your code:

```python
from octobot.utils import signal_client

# Create client from OctoBot config
client = signal_client.create_signal_client_from_config(config)

# Get cached signal without fetching
cached = client.get_cached_signal()
```

## Safety Features

1. **Freshness Validation**: Only acts on recent signals
2. **Network Fallback**: Uses cached signals on network failure
3. **Bias Thresholds**: Filters low-confidence signals
4. **Position Sizing**: Limits risk per trade
5. **Automatic Stop-Loss**: Always sets 1% stop-loss
6. **Time-Based Exit**: Closes position at close_time
7. **Error Handling**: Continues operation despite errors

## Limitations

- Polling-based (not real-time push notifications)
- Single signal per poll (no batch processing)
- Requires signal backend to be running
- Network latency affects signal freshness

## Future Enhancements

Possible improvements:
- WebSocket support for real-time signals
- Multi-symbol signal handling
- Signal history and analytics
- Dynamic position sizing based on confidence
- Risk management rules
- Backtesting support

## Support

For issues or questions:
1. Check OctoBot logs
2. Verify signal format
3. Test endpoint manually
4. Review configuration

## License

This module is part of OctoBot and follows the same license terms.

