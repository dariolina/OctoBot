#!/usr/bin/env python3
"""
Example backend server for External Signal Strategy

This is a simple FastAPI server that serves trading signals.
Replace the logic in get_latest_signal() with your AI Agent Swarm logic.

Requirements:
    pip install fastapi uvicorn

Usage:
    python example_backend.py
    
Then configure OctoBot with:
    "url": "http://localhost:8000/latest"
"""

from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

app = FastAPI(
    title="AI Agent Swarm Crypto Signal API",
    description="External signal provider for OctoBot",
    version="1.0.0"
)


class Signal(BaseModel):
    """Trading signal model for spot trading"""
    pair: str = Field(..., description="Trading pair (e.g., 'BTC-USDC')")
    timestamp: str = Field(..., description="ISO 8601 timestamp (UTC)")
    action: str = Field(..., description="One of: 'buy', 'sell', 'no-trade'")
    bias: float = Field(..., ge=0.0, le=100.0, description="Signal confidence/bias as percentage (0 to 100)")
    close_time: str = Field(..., description="ISO 8601 timestamp when position should be closed (UTC)")
    market_id: str = Field(..., description="Unique identifier for this signal to prevent duplicate trades")
    reason: Optional[str] = Field(None, description="Human-readable reason for the signal")


# In-memory storage for demo purposes
# Replace with your actual signal generation logic
_now = datetime.now(timezone.utc)
latest_signals = {
    "BTC-USDC": Signal(
        pair="BTC-USDC",
        timestamp=_now.isoformat(),
        action="buy",
        bias=75.0,
        close_time=(_now + timedelta(hours=4)).isoformat(),
        market_id=f"btc-usdc-{int(_now.timestamp())}",
        reason="AI consensus: Strong bullish momentum detected"
    )
}


@app.get("/latest", response_model=Signal)
async def get_latest_signal(pair: Optional[str] = Query(None, description="Filter by pair")):
    """
    Get the latest trading signal.
    
    Args:
        pair: Optional pair filter (e.g., "BTC-USDC")
        
    Returns:
        Latest signal for the specified pair or default pair
    """
    # If pair specified, return signal for that pair
    if pair:
        if pair in latest_signals:
            return latest_signals[pair]
        # Return a no-trade signal if pair not found
        now = datetime.now(timezone.utc)
        return Signal(
            pair=pair,
            timestamp=now.isoformat(),
            action="no-trade",
            bias=0.0,
            close_time=(now + timedelta(hours=1)).isoformat(),
            market_id=f"{pair.lower()}-notrade-{int(now.timestamp())}",
            reason=f"No signal available for {pair}"
        )
    
    # Return default signal (BTC-USDC)
    default_pair = "BTC-USDC"
    if default_pair in latest_signals:
        # Update timestamp to current time
        signal = latest_signals[default_pair]
        signal.timestamp = datetime.now(timezone.utc).isoformat()
        return signal
    
    # Fallback: generate a new signal
    return generate_ai_signal(default_pair)


def generate_ai_signal(pair: str) -> Signal:
    """
    Generate a trading signal using AI Agent Swarm logic.
    
    NOTE: This is a demo implementation. For production, your endpoint
    at https://agents.eternax.ai/spot_signal will provide real signals.
    
    Args:
        pair: Trading pair to generate signal for
        
    Returns:
        Generated trading signal
    """
    # Example: This is demo logic for local testing
    # In production, signals come from https://agents.eternax.ai/spot_signal
    
    import random
    
    # Demo logic: Random signals (replace with real AI!)
    actions = ["buy", "sell", "no-trade"]
    weights = [0.3, 0.2, 0.5]  # Bias towards no-trade for safety
    action = random.choices(actions, weights=weights)[0]
    
    if action == "no-trade":
        bias = 0.0
        reason = "No clear signal from AI agents"
    else:
        bias = random.uniform(50.0, 90.0)
        reason = f"AI consensus: {action.upper()} signal with {bias:.1f}% bias"
    
    # Set close_time to 2-6 hours from now
    now = datetime.now(timezone.utc)
    close_hours = random.uniform(2, 6)
    close_time = now + timedelta(hours=close_hours)
    
    # Generate unique market_id
    market_id = f"{pair.lower().replace('-', '')}-{action}-{int(now.timestamp())}"
    
    return Signal(
        pair=pair,
        timestamp=now.isoformat(),
        action=action,
        bias=bias,
        close_time=close_time.isoformat(),
        market_id=market_id,
        reason=reason
    )


@app.post("/update")
async def update_signal(signal: Signal):
    """
    Update the latest signal for a pair.
    
    This endpoint allows you to push new signals to the server.
    Useful for integrating with external AI systems.
    
    Args:
        signal: New signal to store
        
    Returns:
        Success message
    """
    latest_signals[signal.pair] = signal
    return {"message": f"Signal updated for {signal.pair}", "signal": signal}


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "signals_count": len(latest_signals)
    }


@app.get("/error_example")
async def error_example():
    """
    Example of error response format.
    
    When your AI system cannot generate a signal (e.g., insufficient data,
    market conditions unclear, API failure), return this format.
    
    OctoBot will log the error and continue polling without attempting a trade.
    """
    return {
        "pair": "BTC-USDC",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "error": "No signal available",
        "message": "Insufficient market data for signal generation",
        "action": "no-trade"
    }


@app.get("/")
async def root():
    """API information"""
    return {
        "name": "AI Agent Swarm Crypto Signal API",
        "version": "1.0.0",
        "endpoints": {
            "latest": "/latest (GET)",
            "update": "/update (POST)",
            "health": "/health (GET)"
        },
        "documentation": "/docs"
    }


if __name__ == "__main__":
    import uvicorn
    
    print("=" * 60)
    print("AI Agent Swarm Crypto Signal Server")
    print("=" * 60)
    print()
    print("Starting server on http://localhost:8000")
    print()
    print("Endpoints:")
    print("  - http://localhost:8000/latest       (Get latest signal)")
    print("  - http://localhost:8000/docs         (API documentation)")
    print("  - http://localhost:8000/health       (Health check)")
    print()
    print("Configure OctoBot with:")
    print('  "url": "http://localhost:8000/latest"')
    print()
    print("=" * 60)
    print()
    
    uvicorn.run(app, host="0.0.0.0", port=8000)

