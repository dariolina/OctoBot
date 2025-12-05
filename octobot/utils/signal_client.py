#  Drakkar-Software OctoBot
#  Copyright (c) Drakkar-Software, All rights reserved.
#
#  This library is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3.0 of the License, or (at your option) any later version.
#
#  This library is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public
#  License along with this library.

import asyncio
import aiohttp
import json
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import octobot_commons.logging as logging


class ExternalSignalClient:
    """
    Client for fetching external trading signals from REST API.
    Implements caching and validation for AI Agent Swarm signals.
    """
    
    VALID_ACTIONS = {"long", "short", "no-trade"}
    
    def __init__(self, url: str, freshness_seconds: int = 600, timeout: int = 10):
        """
        Initialize the external signal client.
        
        Args:
            url: REST endpoint URL to fetch signals from
            freshness_seconds: Maximum age of signal in seconds (default 600 = 10 minutes)
            timeout: HTTP request timeout in seconds
        """
        self.url = url
        self.freshness_seconds = freshness_seconds
        self.timeout = timeout
        self.logger = logging.get_logger(self.__class__.__name__)
        
        # Cache
        self._cached_signal: Optional[Dict[str, Any]] = None
        self._cache_time: Optional[float] = None
        self._fetch_lock = asyncio.Lock()
        
    async def get_signal(self, symbol: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Fetch the latest signal from the external backend.
        Returns cached signal on network failure.
        
        Args:
            symbol: Optional symbol filter (e.g., "BTC-USDT")
            
        Returns:
            Signal dict with keys: symbol, timestamp, action, confidence, reason, tp_pct, sl_pct
            Returns None if no valid signal available
        """
        async with self._fetch_lock:
            try:
                async with aiohttp.ClientSession() as session:
                    params = {"symbol": symbol} if symbol else None
                    async with session.get(
                        self.url,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=self.timeout)
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            if self._validate_signal(data):
                                self._cached_signal = data
                                self._cache_time = time.time()
                                self.logger.info(
                                    f"Fetched external signal: {data.get('action')} "
                                    f"for {data.get('symbol')} "
                                    f"(confidence: {data.get('confidence', 0):.2%})"
                                )
                                return data
                            else:
                                self.logger.warning(f"Invalid signal format received: {data}")
                        else:
                            self.logger.error(
                                f"Failed to fetch signal: HTTP {response.status}"
                            )
            except asyncio.TimeoutError:
                self.logger.warning(f"Signal fetch timeout after {self.timeout}s")
            except aiohttp.ClientError as e:
                self.logger.warning(f"Network error fetching signal: {e}")
            except json.JSONDecodeError as e:
                self.logger.error(f"Invalid JSON response: {e}")
            except Exception as e:
                self.logger.exception(e, True, f"Unexpected error fetching signal: {e}")
            
            # Return cached signal if available and still fresh
            if self._cached_signal and self._is_signal_fresh(self._cached_signal):
                self.logger.debug("Using cached signal due to fetch failure")
                return self._cached_signal
            
            return None
    
    def _validate_signal(self, data: Dict[str, Any]) -> bool:
        """Validate signal structure and required fields."""
        required_fields = ["symbol", "timestamp", "action", "confidence", "tp_pct", "sl_pct"]
        
        if not isinstance(data, dict):
            return False
        
        for field in required_fields:
            if field not in data:
                self.logger.error(f"Missing required field: {field}")
                return False
        
        # Validate action
        if data["action"] not in self.VALID_ACTIONS:
            self.logger.error(f"Invalid action: {data['action']}, expected one of {self.VALID_ACTIONS}")
            return False
        
        # Validate numeric fields
        try:
            float(data["confidence"])
            float(data["tp_pct"])
            float(data["sl_pct"])
        except (ValueError, TypeError):
            self.logger.error("Invalid numeric values in signal")
            return False
        
        return True
    
    def _is_signal_fresh(self, signal: Dict[str, Any]) -> bool:
        """Check if signal is within freshness window."""
        try:
            signal_time = datetime.fromisoformat(
                signal["timestamp"].replace("Z", "+00:00")
            )
            age_seconds = (datetime.now(timezone.utc) - signal_time).total_seconds()
            
            if age_seconds > self.freshness_seconds:
                self.logger.debug(
                    f"Signal is stale: {age_seconds:.0f}s old "
                    f"(max: {self.freshness_seconds}s)"
                )
                return False
            
            return True
        except (ValueError, KeyError) as e:
            self.logger.error(f"Error parsing signal timestamp: {e}")
            return False
    
    def is_signal_actionable(self, signal: Optional[Dict[str, Any]]) -> bool:
        """
        Check if signal is actionable (fresh and not 'no-trade').
        
        Args:
            signal: Signal dict to check
            
        Returns:
            True if signal should trigger a trade
        """
        if not signal:
            return False
        
        action = signal.get("action")
        if action == "no-trade":
            self.logger.debug("Signal action is 'no-trade'")
            return False
        
        if not self._is_signal_fresh(signal):
            return False
        
        return action in ["long", "short"]
    
    def get_cached_signal(self) -> Optional[Dict[str, Any]]:
        """Get the last cached signal without fetching."""
        return self._cached_signal if self._cached_signal and self._is_signal_fresh(self._cached_signal) else None


def create_signal_client_from_config(config: dict) -> Optional[ExternalSignalClient]:
    """
    Factory function to create signal client from OctoBot config.
    
    Args:
        config: OctoBot configuration dict
        
    Returns:
        ExternalSignalClient instance or None if not configured
    """
    external_signal_config = config.get("external_signal", {})
    
    if not external_signal_config.get("enabled", False):
        return None
    
    url = external_signal_config.get("url")
    if not url:
        logging.get_logger("ExternalSignalClient").warning(
            "External signal enabled but no URL configured"
        )
        return None
    
    freshness_seconds = external_signal_config.get("freshness_seconds", 600)
    timeout = external_signal_config.get("timeout", 10)
    
    return ExternalSignalClient(url, freshness_seconds, timeout)

