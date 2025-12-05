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

from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

app = FastAPI(
    title="AI Agent Swarm Crypto Signal API",
    description="External signal provider for OctoBot",
    version="1.0.0"
)


class Signal(BaseModel):
    """Trading signal model"""
    symbol: str = Field(..., description="Trading pair (e.g., 'BTC-USDT')")
    timestamp: str = Field(..., description="ISO 8601 timestamp (UTC)")
    action: str = Field(..., description="One of: 'long', 'short', 'no-trade'")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence level (0.0 to 1.0)")
    reason: str = Field(..., description="Human-readable reason for the signal")
    tp_pct: float = Field(..., gt=0.0, description="Take-profit percentage (e.g., 0.01 = 1%)")
    sl_pct: float = Field(..., gt=0.0, description="Stop-loss percentage (e.g., 0.01 = 1%)")


# In-memory storage for demo purposes
# Replace with your actual signal generation logic
latest_signals = {
    "BTC-USDT": Signal(
        symbol="BTC-USDT",
        timestamp=datetime.now(timezone.utc).isoformat(),
        action="long",
        confidence=0.75,
        reason="AI consensus: Strong bullish momentum detected",
        tp_pct=0.015,
        sl_pct=0.01
    )
}


@app.get("/latest", response_model=Signal)
async def get_latest_signal(symbol: Optional[str] = Query(None, description="Filter by symbol")):
    """
    Get the latest trading signal.
    
    Args:
        symbol: Optional symbol filter (e.g., "BTC-USDT")
        
    Returns:
        Latest signal for the specified symbol or default symbol
    """
    # If symbol specified, return signal for that symbol
    if symbol:
        if symbol in latest_signals:
            return latest_signals[symbol]
        # Return a no-trade signal if symbol not found
        return Signal(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc).isoformat(),
            action="no-trade",
            confidence=0.0,
            reason=f"No signal available for {symbol}",
            tp_pct=0.01,
            sl_pct=0.01
        )
    
    # Return default signal (BTC-USDT)
    default_symbol = "BTC-USDT"
    if default_symbol in latest_signals:
        # Update timestamp to current time
        signal = latest_signals[default_symbol]
        signal.timestamp = datetime.now(timezone.utc).isoformat()
        return signal
    
    # Fallback: generate a new signal
    return generate_ai_signal(default_symbol)


def generate_ai_signal(symbol: str) -> Signal:
    """
    Generate a trading signal using AI Agent Swarm logic.
    
    REPLACE THIS WITH YOUR ACTUAL AI LOGIC!
    
    Args:
        symbol: Trading pair to generate signal for
        
    Returns:
        Generated trading signal
    """
    # Example: This is where you would call your AI agent swarm
    # For now, this is just a placeholder that returns a demo signal
    
    import random
    
    # Demo logic: Random signals (replace with real AI!)
    actions = ["long", "short", "no-trade"]
    weights = [0.3, 0.2, 0.5]  # Bias towards no-trade for safety
    action = random.choices(actions, weights=weights)[0]
    
    if action == "no-trade":
        confidence = 0.0
        reason = "No clear signal from AI agents"
    else:
        confidence = random.uniform(0.5, 0.9)
        reason = f"AI consensus: {action.upper()} signal with {confidence:.1%} confidence"
    
    return Signal(
        symbol=symbol,
        timestamp=datetime.now(timezone.utc).isoformat(),
        action=action,
        confidence=confidence,
        reason=reason,
        tp_pct=random.uniform(0.01, 0.02),  # 1-2% TP
        sl_pct=random.uniform(0.005, 0.015)  # 0.5-1.5% SL
    )


@app.post("/update")
async def update_signal(signal: Signal):
    """
    Update the latest signal for a symbol.
    
    This endpoint allows you to push new signals to the server.
    Useful for integrating with external AI systems.
    
    Args:
        signal: New signal to store
        
    Returns:
        Success message
    """
    latest_signals[signal.symbol] = signal
    return {"message": f"Signal updated for {signal.symbol}", "signal": signal}


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "signals_count": len(latest_signals)
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

