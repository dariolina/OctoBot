# External Signal Strategy for OctoBot

This module provides integration with external AI trading signals, allowing OctoBot to execute trades based on signals from your custom "AI Agent Swarm Crypto Signal" backend.

## Overview

The External Signal Strategy consists of three main components:

1. **Signal Client** (`octobot/utils/signal_client.py`): Fetches and validates signals from your REST API
2. **Strategy Evaluator** (`strategy_evaluator.py`): Polls for signals and evaluates trading opportunities
3. **Trading Mode** (`trading_mode.py`): Executes trades with take-profit and stop-loss levels

## Features

- Fetches signals from a configurable REST endpoint
- Supports long, short, and no-trade signals
- Automatic signal freshness validation (configurable, default 10 minutes)
- Built-in caching with fallback on network failures
- Configurable confidence thresholds
- Automatic take-profit and stop-loss order placement
- Works with both spot and futures trading

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
  "min_confidence": 0.5,
  "enable_long": true,
  "enable_short": true
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
| `min_confidence` | number | 0.5 | Minimum signal confidence (0.0 to 1.0) |
| `enable_long` | boolean | true | Enable long trades |
| `enable_short` | boolean | true | Enable short trades |

## Signal Format

Your REST API endpoint should return JSON in the following format:

```json
{
  "symbol": "BTC-USDT",
  "timestamp": "2025-12-03T07:40:00Z",
  "action": "long",
  "confidence": 0.71,
  "reason": "Strong upward bias 71.4%",
  "tp_pct": 0.01,
  "sl_pct": 0.01
}
```

### Signal Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `symbol` | string | Yes | Trading pair (e.g., "BTC-USDT" or "BTC/USDT") |
| `timestamp` | string | Yes | ISO 8601 timestamp (UTC) |
| `action` | string | Yes | One of: "long", "short", "no-trade" |
| `confidence` | number | Yes | Confidence level (0.0 to 1.0) |
| `reason` | string | No | Human-readable reason for the signal |
| `tp_pct` | number | Yes | Take-profit percentage (e.g., 0.01 = 1%) |
| `sl_pct` | number | Yes | Stop-loss percentage (e.g., 0.01 = 1%) |

### Action Values

- **`long`**: Open a long position (buy)
- **`short`**: Open a short position (sell)
- **`no-trade`**: No action (signal is ignored)

## How It Works

### 1. Signal Polling

The strategy evaluator polls your REST endpoint at the configured interval (default: every 60 seconds).

### 2. Signal Validation

Each signal is validated for:
- Required fields presence
- Valid action type
- Numeric field validity
- Timestamp freshness

### 3. Signal Processing

If a signal is valid and actionable:
- The signal is cached
- The trading mode is notified
- Orders are created with TP/SL levels

### 4. Trade Execution

For **long** signals:
- Market buy order is created
- Take-profit: `entry_price × (1 + tp_pct)`
- Stop-loss: `entry_price × (1 - sl_pct)`

For **short** signals:
- Market sell order is created
- Take-profit: `entry_price × (1 - tp_pct)`
- Stop-loss: `entry_price × (1 + sl_pct)`

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
  "min_confidence": 0.7,
  "enable_long": true,
  "enable_short": false
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
Fetched external signal: long for BTC-USDT (confidence: 71.00%)
Executing LONG trade for BTC/USDT
Long order created: BTC/USDT qty=0.01 TP=51500.00 SL=49500.00
```

## Troubleshooting

### No trades being executed

1. Check that `external_signal.enabled` is `true`
2. Verify your signal endpoint is accessible
3. Check signal freshness (timestamp within freshness_seconds)
4. Verify confidence meets min_confidence threshold
5. Check logs for validation errors

### Network errors

- Increase `timeout` value
- Check firewall/network connectivity
- Verify URL is correct
- Check signal endpoint logs

### Invalid signal format

- Ensure all required fields are present
- Verify timestamp is ISO 8601 format
- Check action is one of: "long", "short", "no-trade"
- Ensure tp_pct and sl_pct are numeric

### Orders not created

- Check exchange connectivity
- Verify sufficient balance
- Check minimum order size for the exchange
- Review trading mode activation

## API Integration Example

Example Python backend for serving signals:

```python
from fastapi import FastAPI
from datetime import datetime, timezone
from pydantic import BaseModel

app = FastAPI()

class Signal(BaseModel):
    symbol: str
    timestamp: str
    action: str
    confidence: float
    reason: str
    tp_pct: float
    sl_pct: float

@app.get("/latest")
async def get_latest_signal():
    # Your AI agent swarm logic here
    return Signal(
        symbol="BTC-USDT",
        timestamp=datetime.now(timezone.utc).isoformat(),
        action="long",
        confidence=0.75,
        reason="AI consensus: strong bullish signal",
        tp_pct=0.015,
        sl_pct=0.01
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
│  - Create orders        │
│  - Set TP/SL            │
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

# Fetch signal for specific symbol
signal = await client.get_signal(symbol="BTC-USDT")

# Check if actionable
if client.is_signal_actionable(signal):
    print(f"Trade: {signal['action']} {signal['symbol']}")
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
3. **Confidence Thresholds**: Filters low-confidence signals
4. **Position Sizing**: Limits risk per trade
5. **Automatic TP/SL**: Always sets exit levels
6. **Error Handling**: Continues operation despite errors

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

