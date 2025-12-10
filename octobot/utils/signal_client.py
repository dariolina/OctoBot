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
    Implements caching and validation for spot trading signals.
    Tracks market_id to ensure each signal is only traded once.
    """
    
    VALID_ACTIONS = {"buy", "sell", "no-trade"}
    
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
        
        # Track processed market IDs to avoid duplicate trades
        self._processed_market_ids: set = set()
        self._last_market_id: Optional[str] = None
        
    async def get_signal(self, pair: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Fetch the latest signal from the external backend.
        Returns cached signal on network failure.
        
        Args:
            pair: Optional pair filter (e.g., "BTC-USDC")
            
        Returns:
            Signal dict with keys: pair, timestamp, action, bias, close_time, reason
            Returns None if no valid signal available
        """
        async with self._fetch_lock:
            try:
                async with aiohttp.ClientSession() as session:
                    params = {"pair": pair} if pair else None
                    async with session.get(
                        self.url,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=self.timeout)
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            
                            # Check if response is an error
                            if self._is_error_response(data):
                                error_msg = data.get("error", "Unknown error")
                                message = data.get("message", "")
                                self.logger.warning(
                                    f"Endpoint returned error: {error_msg}"
                                    f"{' - ' + message if message else ''}"
                                )
                                # Return None to indicate no valid signal
                                return None
                            
                            if self._validate_signal(data):
                                # Check if this is a new signal (different market_id)
                                market_id = data.get("market_id")
                                if market_id and market_id == self._last_market_id:
                                    self.logger.debug(
                                        f"Signal with market_id {market_id} already processed, skipping"
                                    )
                                    # Return cached signal but mark it as already processed
                                    return self._cached_signal
                                
                                # New signal, update cache
                                self._cached_signal = data
                                self._cache_time = time.time()
                                
                                # Track this market_id
                                if market_id:
                                    self._last_market_id = market_id
                                    self._processed_market_ids.add(market_id)
                                
                                self.logger.info(
                                    f"Fetched external signal: {data.get('action')} "
                                    f"for {data.get('pair')} "
                                    f"(bias: {data.get('bias', 0):.1f}%, market_id: {market_id})"
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
    
    def _is_error_response(self, data: Dict[str, Any]) -> bool:
        """
        Check if the response is an error message from the endpoint.
        
        Error format:
        {
            "pair": "BTC-USDC",
            "timestamp": "...",
            "error": "No signal available",
            "message": "reason",
            "action": "no-trade"
        }
        
        Args:
            data: Response data to check
            
        Returns:
            True if this is an error response
        """
        return "error" in data and data.get("action") == "no-trade"
    
    def _validate_signal(self, data: Dict[str, Any]) -> bool:
        """Validate signal structure and required fields."""
        if not isinstance(data, dict):
            return False
        
        # Check basic required fields
        basic_required = ["pair", "timestamp", "action"]
        for field in basic_required:
            if field not in data:
                self.logger.error(f"Missing required field: {field}")
                return False
        
        # Validate action
        if data["action"] not in self.VALID_ACTIONS:
            self.logger.error(f"Invalid action: {data['action']}, expected one of {self.VALID_ACTIONS}")
            return False
        
        # If action is "no-trade" and it's not an error response, we still need all fields
        # If it's a buy/sell action, validate all required fields
        if data["action"] in ["buy", "sell"]:
            signal_required = ["bias", "close_time", "market_id"]
            for field in signal_required:
                if field not in data:
                    self.logger.error(f"Missing required field for {data['action']} signal: {field}")
                    return False
            
            # Validate numeric fields
            try:
                bias = float(data["bias"])
                if not (0 <= bias <= 100):
                    self.logger.error(f"Invalid bias value: {bias}, must be between 0 and 100")
                    return False
            except (ValueError, TypeError):
                self.logger.error("Invalid numeric values in signal")
                return False
            
            # Validate timestamps
            try:
                datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
                datetime.fromisoformat(data["close_time"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                self.logger.error("Invalid timestamp format in signal")
                return False
            
            # Validate market_id is present and not empty
            if not data["market_id"] or not str(data["market_id"]).strip():
                self.logger.error("market_id field is empty")
                return False
        
        elif data["action"] == "no-trade":
            # For no-trade signals, we're more lenient
            # Just validate timestamp if present
            try:
                datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                self.logger.error("Invalid timestamp format in signal")
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
        Check if signal is actionable (fresh, not 'no-trade', and not already processed).
        
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
        
        # Check if this market_id was already processed
        market_id = signal.get("market_id")
        if market_id and market_id in self._processed_market_ids:
            self.logger.debug(f"Signal with market_id {market_id} already processed")
            return False
        
        return action in ["buy", "sell"]
    
    def get_cached_signal(self) -> Optional[Dict[str, Any]]:
        """Get the last cached signal without fetching."""
        return self._cached_signal if self._cached_signal and self._is_signal_fresh(self._cached_signal) else None
    
    def mark_signal_processed(self, market_id: str) -> None:
        """
        Mark a signal as processed to prevent duplicate trades.
        
        Args:
            market_id: The market_id of the signal that was processed
        """
        if market_id:
            self._processed_market_ids.add(market_id)
            self._last_market_id = market_id
            self.logger.debug(f"Marked market_id {market_id} as processed")
            
            # Keep only the last 1000 market_ids to prevent memory growth
            if len(self._processed_market_ids) > 1000:
                # Remove oldest half of the set
                ids_to_remove = list(self._processed_market_ids)[:500]
                for old_id in ids_to_remove:
                    self._processed_market_ids.discard(old_id)
                self.logger.debug(f"Cleaned up old market_ids, kept {len(self._processed_market_ids)}")
    
    def is_signal_new(self, signal: Dict[str, Any]) -> bool:
        """
        Check if a signal has not been processed yet based on market_id.
        
        Args:
            signal: Signal dict to check
            
        Returns:
            True if signal is new (not yet processed)
        """
        market_id = signal.get("market_id")
        if not market_id:
            return True  # If no market_id, consider it new (shouldn't happen with validation)
        
        return market_id not in self._processed_market_ids


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

