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

"""
External Signal Strategy Evaluator

This evaluator fetches trading signals from an external REST API endpoint
and generates trading recommendations based on those signals.

Signal Format (Spot Trading):
- pair: Trading pair (e.g., "BTC-USDC")
- action: "buy", "sell", or "no-trade"
- bias: Confidence percentage (0-100)
- close_time: ISO timestamp when position should be closed
- timestamp: ISO timestamp of signal generation

To use this strategy:
1. Add configuration to your config.json:
   {
     "external_signal": {
       "enabled": true,
       "url": "http://localhost:8000/latest",
       "freshness_seconds": 600,
       "timeout": 10,
       "poll_interval_seconds": 60
     }
   }

2. Activate this strategy in your tentacles configuration

3. Use with the ExternalSignalTradingMode
"""

import asyncio
from typing import Optional, Dict, Any

try:
    import octobot_evaluators.evaluators as evaluators
    import octobot_evaluators.enums as evaluator_enums
    import octobot_evaluators.api.matrix as evaluators_api
    import octobot_evaluators.matrix as matrix
    import octobot_commons.constants as commons_constants
    import octobot_commons.evaluators_util as evaluators_util
except ImportError:
    # Handle case where evaluators package is not available
    class evaluators:
        class StrategyEvaluator:
            pass
    
    class evaluator_enums:
        class EvaluatorMatrixTypes:
            STRATEGIES = "strategies"

import octobot_commons.logging as logging
import octobot.utils.signal_client as signal_client
import octobot_trading.api as trading_api
import octobot.commands as commands


class ExternalSignalStrategyEvaluator(evaluators.StrategyEvaluator):
    """
    Strategy evaluator that uses external spot trading signals.
    
    Fetches signals periodically and evaluates them for trading decisions.
    Supports buy, sell, and no-trade signals with bias (confidence) and close_time.
    """
    
    def __init__(self, tentacles_setup_config):
        super().__init__(tentacles_setup_config)
        self.signal_client: Optional[signal_client.ExternalSignalClient] = None
        self.poll_interval = 60  # seconds
        self._signal_fetch_task = None
        self._last_signal: Optional[Dict[str, Any]] = None
    
    async def matrix_callback(self, matrix_id, evaluator_name, evaluator_type,
                             eval_note, eval_note_type, exchange_name, cryptocurrency, symbol, time_frame):
        """
        Callback triggered by the evaluator matrix.
        Not used by this strategy as we poll external signals instead.
        """
        pass
    
    async def start(self, bot_id: str) -> bool:
        """
        Start the strategy evaluator.
        Initializes signal client and starts polling task.
        """
        await super().start(bot_id)
        
        # Initialize signal client from config
        # Get config from global bot instance
        if commands.GLOBAL_BOT_INSTANCE:
            config = commands.GLOBAL_BOT_INSTANCE.config
        else:
            # Fallback: try to get from tentacles_setup_config if available
            self.logger.warning("Cannot access bot config, external signal client may not work properly")
            config = {}
        self.signal_client = signal_client.create_signal_client_from_config(config)
        
        if not self.signal_client:
            self.logger.warning(
                "External signal strategy is active but external_signal is not enabled in config. "
                "This strategy will not generate any signals."
            )
            return True
        
        # Get poll interval from config
        external_config = config.get("external_signal", {})
        self.poll_interval = external_config.get("poll_interval_seconds", 60)
        
        # Start signal polling task
        self._signal_fetch_task = asyncio.create_task(self._signal_polling_loop())
        
        self.logger.info(
            f"External signal strategy started. "
            f"Polling every {self.poll_interval}s from {self.signal_client.url}"
        )
        
        # Do an initial fetch immediately (don't wait for first poll interval)
        try:
            self.logger.info("Performing initial signal fetch...")
            signal = await self.signal_client.get_signal()
            if signal:
                self._last_signal = signal
                if self.signal_client.is_signal_actionable(signal):
                    self.logger.info(f"Initial signal is actionable, processing: {signal.get('market_id')}")
                    await self._process_signal(signal)
        except Exception as e:
            self.logger.warning(f"Initial signal fetch failed (will retry in polling loop): {e}")
        
        return True
    
    async def stop(self):
        """Stop the strategy evaluator and cancel polling task."""
        if self._signal_fetch_task and not self._signal_fetch_task.done():
            self._signal_fetch_task.cancel()
            try:
                await self._signal_fetch_task
            except asyncio.CancelledError:
                pass
        
        await super().stop()
    
    async def _signal_polling_loop(self):
        """Continuously poll for new signals at the configured interval."""
        self.logger.info(f"Starting signal polling loop (interval: {self.poll_interval}s)")
        while True:
            try:
                await asyncio.sleep(self.poll_interval)
                
                self.logger.debug(f"Polling for new signal from {self.signal_client.url}")
                
                # Fetch latest signal
                signal = await self.signal_client.get_signal()
                
                if signal:
                    self._last_signal = signal
                    
                    # Trigger evaluation for all symbols if signal is actionable
                    if self.signal_client.is_signal_actionable(signal):
                        self.logger.debug(f"Signal is actionable, processing: {signal.get('market_id')}")
                        await self._process_signal(signal)
                    else:
                        self.logger.debug(
                            f"Signal not actionable: action={signal.get('action')}, "
                            f"market_id={signal.get('market_id')}, "
                            f"fresh={self.signal_client._is_signal_fresh(signal) if signal else False}"
                        )
                else:
                    self.logger.debug("No signal received from endpoint")
                
            except asyncio.CancelledError:
                self.logger.info("Signal polling stopped (cancelled)")
                break
            except Exception as e:
                self.logger.exception(e, True, f"Error in signal polling loop: {e}")
                # Continue polling even after error - wait a bit before retrying
                self.logger.warning(f"Will retry polling in {self.poll_interval}s")
                await asyncio.sleep(self.poll_interval)
    
    async def _process_signal(self, signal: Dict[str, Any]):
        """
        Process an actionable signal and update the evaluation matrix.
        
        Args:
            signal: Signal dict with action, pair, bias, close_time, etc.
        """
        action = signal.get("action")
        pair = signal.get("pair")
        bias = signal.get("bias", 50.0)
        
        # Convert bias (0-100) to eval note (-1 to 1)
        # For buy: positive value based on bias
        # For sell: negative value
        confidence = bias / 100.0
        
        if action == "buy":
            eval_note = confidence  # Positive value for buy
        elif action == "sell":
            eval_note = -confidence  # Negative value for sell
        else:
            eval_note = 0  # Neutral
        
        self.logger.info(
            f"Processing signal: {action} {pair} "
            f"(bias: {bias:.1f}%, "
            f"close_time: {signal.get('close_time', 'N/A')})"
        )
        
        # Update evaluation matrix
        try:
            await self.eval_impl(eval_note, signal)
        except Exception as e:
            self.logger.exception(e, True, f"Error updating evaluation: {e}")
    
    async def eval_impl(self, eval_note: float, signal: Dict[str, Any] = None):
        """
        Implementation of the evaluation logic.
        
        Args:
            eval_note: Evaluation score (-1 to 1, negative for sell, positive for buy)
            signal: Optional signal data for context
        """
        # Store signal data for the trading mode to access
        if signal:
            self.eval_note_time_to_live = self.signal_client.freshness_seconds
            
            # Set evaluation note (required for strategy_completed to work)
            self.eval_note = eval_note
            
            # Notify trading mode that strategy evaluation is complete
            symbol = signal.get("pair", "").replace("-", "/")
            
            # Use self.cryptocurrency if available (set by OctoBot based on config)
            # This should be set automatically by OctoBot from the profile configuration
            if hasattr(self, 'cryptocurrency') and self.cryptocurrency:
                cryptocurrency = self.cryptocurrency
            elif hasattr(self, 'exchange_manager') and self.exchange_manager:
                # Try to get cryptocurrency name from exchange manager config
                try:
                    import octobot_trading.api as trading_api
                    exchange_config = trading_api.get_exchange_configuration_from_exchange_id(
                        self.exchange_manager.exchange_id
                    )
                    # Find cryptocurrency name from symbols_by_crypto_currencies
                    base_code = symbol.split("/")[0]
                    for crypto_name, pairs in exchange_config.symbols_by_crypto_currencies.items():
                        if any(pair.startswith(base_code + "/") for pair in pairs):
                            cryptocurrency = crypto_name
                            break
                    else:
                        # Fallback: use code-to-name mapping
                        raise KeyError("Cryptocurrency not found in config")
                except Exception:
                    # Fallback: map common codes to full names (OctoBot uses full names in config)
                    code_to_name = {
                        "BTC": "Bitcoin",
                        "ETH": "Ethereum",
                        "USDC": "USD Coin",
                        "USDT": "Tether"
                    }
                    base_code = symbol.split("/")[0]
                    cryptocurrency = code_to_name.get(base_code, base_code)
            else:
                # Final fallback: map common codes to full names
                code_to_name = {
                    "BTC": "Bitcoin",
                    "ETH": "Ethereum",
                    "USDC": "USD Coin",
                    "USDT": "Tether"
                }
                base_code = symbol.split("/")[0]
                cryptocurrency = code_to_name.get(base_code, base_code)
            
            self.logger.info(
                f"Setting evaluation: cryptocurrency={cryptocurrency}, symbol={symbol}, eval_note={eval_note}"
            )
            
            # Set eval_note (required for strategy_completed to work)
            self.eval_note = eval_note
            
            # Set evaluation in matrix first (this triggers Producer's set_final_eval)
            # Then call strategy_completed to notify trading modes
            if hasattr(self, 'matrix_id') and self.matrix_id:
                try:
                    # Set evaluation in matrix using the API
                    evaluators_api.set_evaluator_eval(
                        self.matrix_id,
                        self.get_name(),
                        evaluator_enums.EvaluatorMatrixTypes.STRATEGIES.value,
                        self.exchange_name,
                        cryptocurrency,
                        symbol,
                        None,  # time_frame
                        eval_note
                    )
                    self.logger.debug(f"Set evaluation in matrix for {symbol}")
                except Exception as e:
                    self.logger.debug(f"Could not set evaluation in matrix: {e}")
            
            # Call strategy_completed which triggers trading mode callback
            # This is the standard way to notify trading modes when strategy evaluation is complete
            await self.strategy_completed(cryptocurrency, symbol)
    
    def get_signal_data(self) -> Optional[Dict[str, Any]]:
        """
        Get the most recent signal data.
        Used by the trading mode to access bias and close_time.
        """
        if self.signal_client:
            return self.signal_client.get_cached_signal()
        return self._last_signal
    
    @classmethod
    def get_is_cryptocurrency_wildcard(cls) -> bool:
        """This strategy applies to all cryptocurrencies."""
        return True
    
    @classmethod
    def get_is_symbol_wildcard(cls) -> bool:
        """This strategy applies to all symbols."""
        return True
    
    @classmethod
    def get_is_time_frame_wildcard(cls) -> bool:
        """This strategy is not time-frame dependent."""
        return True


# For compatibility with tentacles system
def get_evaluator_classes():
    """Return evaluator classes for tentacles registration."""
    return [ExternalSignalStrategyEvaluator]

