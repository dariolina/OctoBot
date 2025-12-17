# Quick Setup Guide: External Signal Strategy

This is a 5-minute setup guide to get the External Signal Strategy working with OctoBot.

## Prerequisites

- OctoBot installed and running
- Python 3.8+
- Access to modify OctoBot configuration

## Step 1: Start Your Signal Backend (2 minutes)

### Option A: Use the Example Backend

1. Install dependencies:
   ```bash
   pip install fastapi uvicorn
   ```

2. Start the example server:
   ```bash
   python octobot/external_signals/example_backend.py
   ```

3. Test it works:
   ```bash
   curl http://localhost:8000/latest
   ```

You should see a JSON response with a signal.

### Option B: Use Your Own Backend

Make sure your backend returns JSON in this format:

```json
{
  "symbol": "BTC-USDT",
  "timestamp": "2025-12-05T10:00:00Z",
  "action": "long",
  "confidence": 0.75,
  "reason": "Strong bullish signal",
  "tp_pct": 0.015,
  "sl_pct": 0.01
}
```

## Step 2: Configure OctoBot (1 minute)

Add this to your `user/config.json` or `config.json`:

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

If the file doesn't exist, create it with the above content wrapped in `{}`.

## Step 3: Integration Method (2 minutes)

Choose one of these integration methods:

### Method 1: Direct Usage (Simplest)

Use the signal client directly in your custom code:

```python
from octobot.utils.signal_client import ExternalSignalClient

# Create client
client = ExternalSignalClient(
    url="http://localhost:8000/latest",
    freshness_seconds=600
)

# Fetch signal
signal = await client.get_signal()

# Check if actionable
if client.is_signal_actionable(signal):
    print(f"Trade: {signal['action']} {signal['symbol']}")
```

### Method 2: As a Tentacle (Full Integration)

1. Create tentacles directories:
   ```bash
   mkdir -p tentacles/Evaluator/Strategies
   mkdir -p tentacles/Trading/Mode
   ```

2. Copy files:
   ```bash
   cp octobot/external_signals/strategy_evaluator.py \
      tentacles/Evaluator/Strategies/external_signal_strategy_evaluator.py
   
   cp octobot/external_signals/trading_mode.py \
      tentacles/Trading/Mode/external_signal_trading_mode.py
   ```

3. Update tentacles configuration (`user/tentacles_config.json`):
   ```json
   {
     "tentacle_activation": {
       "Evaluator": {
         "ExternalSignalStrategyEvaluator": true
       },
       "Trading": {
         "ExternalSignalTradingMode": true
       }
     }
   }
   ```

4. Restart OctoBot

## Step 4: Verify It Works (1 minute)

1. Check OctoBot logs:
   ```bash
   tail -f logs/OctoBot.log | grep -i "external"
   ```

2. You should see:
   ```
   External signal strategy started. Polling every 60s from http://localhost:8000/latest
   Fetched external signal: long for BTC-USDT (confidence: 75.00%)
   ```

3. If using the full tentacle integration, you should also see:
   ```
   Executing LONG trade for BTC/USDT
   Long order created: BTC/USDT qty=0.01 TP=51500.00 SL=49500.00
   ```

## Troubleshooting

### Signal backend not responding

```bash
# Check if server is running
curl http://localhost:8000/health

# If not, start it:
python octobot/external_signals/example_backend.py
```

### Configuration not loaded

```bash
# Verify JSON syntax
python -m json.tool user/config.json

# Restart OctoBot after config changes
```

### No trades being executed

1. Check signal freshness (timestamp must be recent)
2. Verify confidence meets threshold (default: 0.5)
3. Check action is "long" or "short" (not "no-trade")
4. Review OctoBot logs for errors

## Next Steps

1. **Test with paper trading first!**
   - Configure testnet/paper trading
   - Monitor behavior for a few hours
   - Verify signals trigger correctly

2. **Replace example logic**
   - Update `example_backend.py` with your AI logic
   - Or point to your existing signal backend

3. **Tune configuration**
   - Adjust `poll_interval_seconds` based on signal frequency
   - Set `min_confidence` threshold
   - Configure `position_size_percent`

4. **Monitor performance**
   - Watch logs
   - Track trades
   - Analyze results

## Files Created

This implementation added:

```
octobot/
├── utils/
│   ├── __init__.py                     # Updated
│   └── signal_client.py                # NEW
├── external_signals/
│   ├── __init__.py                     # NEW
│   ├── strategy_evaluator.py          # NEW
│   ├── trading_mode.py                 # NEW
│   ├── config.json                     # NEW
│   ├── metadata.json                   # NEW
│   ├── example_backend.py              # NEW
│   ├── README.md                       # NEW
│   └── SETUP.md                        # This file
└── config/
    └── config_schema.json              # Updated

EXTERNAL_SIGNALS_INTEGRATION.md         # NEW
```

## Configuration Reference

### Minimal Config

```json
{
  "external_signal": {
    "enabled": true,
    "url": "http://localhost:8000/latest"
  }
}
```

### Full Config

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

### Trading Mode Config (if using as tentacle)

```json
{
  "position_size_percent": 10,
  "min_confidence": 0.5,
  "enable_long": true,
  "enable_short": true
}
```

## Safety Tips

1. **Start small**: Use low `position_size_percent` (5-10%)
2. **Test first**: Use paper trading initially
3. **Monitor closely**: Watch logs and trades
4. **Set limits**: Use exchange-level stop-losses as backup
5. **Review signals**: Check signal quality regularly

## Support & Documentation

- **Detailed Documentation**: See `README.md` in this directory
- **Integration Guide**: See `../../EXTERNAL_SIGNALS_INTEGRATION.md`
- **Example Backend**: See `example_backend.py`
- **OctoBot Docs**: https://www.octobot.online/

## Quick Test

Run this to test the signal client:

```python
import asyncio
from octobot.utils.signal_client import ExternalSignalClient

async def test():
    client = ExternalSignalClient(
        url="http://localhost:8000/latest",
        freshness_seconds=600
    )
    signal = await client.get_signal()
    print(f"Signal: {signal}")
    print(f"Actionable: {client.is_signal_actionable(signal)}")

asyncio.run(test())
```

Save as `test_signal.py` and run:
```bash
python test_signal.py
```

## Done!

You now have a working external signal integration. The system will:
- Poll your signal backend every minute
- Validate and cache signals
- Execute trades based on fresh signals with proper TP/SL
- Continue operating even if signal backend is temporarily unavailable

For production use, make sure to:
- Replace the example backend with your real AI logic
- Test thoroughly with paper trading
- Monitor performance and adjust settings
- Implement proper security (HTTPS, authentication)

