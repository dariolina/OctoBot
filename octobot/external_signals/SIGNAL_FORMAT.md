# External Signal Format for Spot Trading

This document describes the signal format used by the External Signal Strategy for spot trading.

## Signal Format

Your external signal endpoint should return JSON in the following format:

```json
{
  "pair": "BTC-USDC",
  "timestamp": "2025-12-06T10:30:00Z",
  "action": "buy",
  "bias": 75.0,
  "close_time": "2025-12-06T14:30:00Z",
  "market_id": "btc-usdc-1733486400",
  "reason": "Strong bullish momentum detected"
}
```

## Field Descriptions

### Required Fields

| Field | Type | Description | Valid Values |
|-------|------|-------------|--------------|
| `pair` | string | Trading pair in format `BASE-QUOTE` | e.g., `"BTC-USDC"`, `"ETH-USDT"` |
| `timestamp` | string | ISO 8601 timestamp (UTC) when signal was generated | e.g., `"2025-12-06T10:30:00Z"` |
| `action` | string | Trading action to take | `"buy"`, `"sell"`, `"no-trade"` |
| `bias` | number | Confidence/bias as percentage | `0.0` to `100.0` |
| `close_time` | string | ISO 8601 timestamp (UTC) when position should be closed | e.g., `"2025-12-06T14:30:00Z"` |
| `market_id` | string | Unique identifier for this signal to prevent duplicate trades | e.g., `"btc-usdc-1733486400"`, `"signal-123"` |

### Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `reason` | string | Human-readable explanation for the signal |

## Trading Behavior

### Buy Signal (`action: "buy"`)

When a buy signal is received:

1. **Entry**: OctoBot buys the base currency (e.g., BTC) using available quote currency (e.g., USDC)
2. **Position Size**: Determined by `position_size_percent` setting (default 10% of available balance)
3. **Stop Loss**: Automatically set at 1% below entry price (configurable via `stop_loss_percent`)
4. **Exit Conditions** (whichever comes first):
   - Price falls below stop-loss level (default 1%) → Sell at market
   - `close_time` is reached → Sell at market

### Sell Signal (`action: "sell"`)

When a sell signal is received:

1. **Exit**: OctoBot sells all available base currency at market price
2. **Cancellation**: Any scheduled close_time tasks for this pair are cancelled

### No-Trade Signal (`action: "no-trade"`)

No action is taken. This is useful for indicating that your system has no recommendation.

## Signal Filtering

Signals are filtered based on:

1. **Freshness**: Signal must be within `freshness_seconds` (default 600s = 10 minutes)
2. **Bias Threshold**: Signal bias must be >= `min_bias` (default 50.0%)
3. **Action**: Must be "buy" or "sell" (not "no-trade")

## Example Signals

### Example 1: High Confidence Buy

```json
{
  "pair": "BTC-USDC",
  "timestamp": "2025-12-06T10:00:00Z",
  "action": "buy",
  "bias": 85.5,
  "close_time": "2025-12-06T16:00:00Z",
  "market_id": "btc-usdc-signal-001",
  "reason": "AI consensus: 85.5% bullish bias with strong momentum"
}
```

**Result**: 
- Buy BTC with USDC balance
- Set stop-loss at -1% from entry
- Automatically sell at market at 16:00 UTC (or earlier if stop-loss hit)

### Example 2: Medium Confidence Buy

```json
{
  "pair": "ETH-USDT",
  "timestamp": "2025-12-06T11:00:00Z",
  "action": "buy",
  "bias": 65.0,
  "close_time": "2025-12-06T19:00:00Z",
  "market_id": "eth-usdt-signal-002",
  "reason": "Moderate bullish signal"
}
```

**Result**:
- Buy ETH with USDT balance
- Set stop-loss at -1% from entry
- Automatically sell at market at 19:00 UTC (or earlier if stop-loss hit)

### Example 3: Immediate Sell

```json
{
  "pair": "BTC-USDC",
  "timestamp": "2025-12-06T12:00:00Z",
  "action": "sell",
  "bias": 90.0,
  "close_time": "2025-12-06T12:00:00Z",
  "market_id": "btc-usdc-exit-001",
  "reason": "Exit signal triggered"
}
```

**Result**:
- Immediately sell all BTC holdings at market price
- Cancel any pending close_time tasks

### Example 4: No Trade

```json
{
  "pair": "BTC-USDC",
  "timestamp": "2025-12-06T13:00:00Z",
  "action": "no-trade",
  "bias": 0.0,
  "close_time": "2025-12-06T14:00:00Z",
  "market_id": "btc-usdc-notrade-001",
  "reason": "Market conditions unclear"
}
```

**Result**: No action taken

### Example 5: Error Response

```json
{
  "pair": "BTC-USDC",
  "timestamp": "2025-12-06T15:00:00Z",
  "error": "No signal available",
  "message": "Insufficient market data for signal generation",
  "action": "no-trade"
}
```

**Result**: Error logged, no action taken, continues polling

## Error Response Format

When your endpoint cannot generate a signal (e.g., API failure, insufficient data), return an error response:

```json
{
  "pair": "BTC-USDC",
  "timestamp": "2025-12-06T10:30:00Z",
  "error": "No signal available",
  "message": "Detailed error reason",
  "action": "no-trade"
}
```

**Error Response Fields:**
- `pair`: Trading pair (required)
- `timestamp`: ISO 8601 timestamp (required)
- `error`: Short error description (required)
- `message`: Detailed error reason (required)
- `action`: Must be `"no-trade"` (required)

**Note:** Error responses don't need `market_id`, `bias`, or `close_time` fields.

OctoBot will:
- Log the error as WARNING
- Not attempt any trades
- Continue polling normally

For complete error response documentation, see `ERROR_RESPONSE_FORMAT.md`.

## Configuration

### OctoBot Configuration

Add to your `user/config.json`:

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

### Trading Mode Settings

Configure the trading mode with these parameters:

- `position_size_percent`: Percentage of portfolio to use per trade (default: 10)
- `min_bias`: Minimum bias threshold 0-100 (default: 50.0)
- `stop_loss_percent`: Stop loss percentage (default: 1.0 = 1%)

## API Endpoint Requirements

Your external signal API should:

1. **Return JSON**: Content-Type must be `application/json`
2. **HTTP 200**: Return status 200 for successful responses
3. **Fast Response**: Respond within timeout period (default 10s)
4. **Optional Filtering**: Support optional `?pair=BTC-USDC` query parameter

### Example Endpoint

```bash
GET http://localhost:8000/latest
GET http://localhost:8000/latest?pair=BTC-USDC
```

### Response Headers

```
HTTP/1.1 200 OK
Content-Type: application/json
```

### Response Body

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

## Validation Rules

OctoBot validates incoming signals and will reject them if:

1. Missing required fields (`pair`, `timestamp`, `action`, `bias`, `close_time`, `market_id`)
2. Invalid `action` value (must be: `"buy"`, `"sell"`, or `"no-trade"`)
3. Invalid `bias` value (must be number between 0 and 100)
4. Invalid `timestamp` or `close_time` format (must be ISO 8601)
5. Signal is too old (older than `freshness_seconds`)
6. `market_id` is empty or missing

## Duplicate Trade Prevention

OctoBot uses the `market_id` field to prevent executing the same signal multiple times:

- Each unique `market_id` is tracked after trade execution
- If the same `market_id` is received again, the signal is skipped
- Prevents accidental duplicate orders from the same signal
- The last 1000 `market_id` values are kept in memory

**Important:** Always provide a unique `market_id` for each distinct signal. Common patterns:
- `"{pair}-{timestamp}"`  → `"btc-usdc-1733486400"`
- `"{pair}-signal-{counter}"` → `"btc-usdc-signal-123"`
- UUID or hash-based IDs

## Error Handling

If signal fetching fails:

1. OctoBot logs the error
2. Falls back to cached signal if available and still fresh
3. Continues polling at `poll_interval_seconds`
4. No trades are executed with stale or invalid signals

## Trading Logic Flow

```
1. External Signal Received
   ↓
2. Validate Signal Format
   ↓
3. Check Freshness (< freshness_seconds)
   ↓
4. Check Bias Threshold (>= min_bias)
   ↓
5. Execute Trade Based on Action
   │
   ├─→ BUY:
   │    • Calculate position size
   │    • Get current price
   │    • Create market buy order
   │    • Set stop-loss at -1%
   │    • Schedule close at close_time
   │
   ├─→ SELL:
   │    • Get available balance
   │    • Create market sell order
   │    • Cancel scheduled close tasks
   │
   └─→ NO-TRADE:
        • Do nothing
```

## Exit Conditions Detail

When a position is opened with a buy signal:

### Stop-Loss Exit (1% below entry)
- **Trigger**: Price falls 1% below entry price
- **Action**: Market sell order immediately
- **Result**: Scheduled close_time task is cancelled

### Time-Based Exit (at close_time)
- **Trigger**: System time reaches close_time
- **Action**: Market sell order at current price
- **Result**: Position is closed regardless of P&L

**Whichever condition is met first will close the position.**

## Notes

- Only spot trading is supported (no leverage, no short selling)
- Each signal should specify a single trading pair
- Multiple active positions across different pairs are supported
- The `bias` field represents your signal's confidence (0-100%)
- Lower `min_bias` = more signals executed (more aggressive)
- Higher `min_bias` = fewer signals executed (more conservative)

