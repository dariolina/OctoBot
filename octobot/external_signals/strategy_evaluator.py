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


class ExternalSignalStrategyEvaluator(evaluators.StrategyEvaluator):
    """
    Strategy evaluator that uses external AI agent swarm signals.
    
    Fetches signals periodically and evaluates them for trading decisions.
    Supports long, short, and no-trade signals with take-profit and stop-loss levels.
    """
    
    def __init__(self):
        super().__init__()
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
        config = self.get_config()
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
        while True:
            try:
                await asyncio.sleep(self.poll_interval)
                
                # Fetch latest signal
                signal = await self.signal_client.get_signal()
                
                if signal:
                    self._last_signal = signal
                    
                    # Trigger evaluation for all symbols if signal is actionable
                    if self.signal_client.is_signal_actionable(signal):
                        await self._process_signal(signal)
                
            except asyncio.CancelledError:
                self.logger.info("Signal polling stopped")
                break
            except Exception as e:
                self.logger.exception(e, True, f"Error in signal polling loop: {e}")
                # Continue polling even after error
    
    async def _process_signal(self, signal: Dict[str, Any]):
        """
        Process an actionable signal and update the evaluation matrix.
        
        Args:
            signal: Signal dict with action, symbol, tp_pct, sl_pct, etc.
        """
        action = signal.get("action")
        symbol = signal.get("symbol")
        confidence = signal.get("confidence", 0.5)
        
        # Convert action to eval note
        if action == "long":
            eval_note = confidence  # Positive value for long
        elif action == "short":
            eval_note = -confidence  # Negative value for short
        else:
            eval_note = 0  # Neutral
        
        self.logger.info(
            f"Processing signal: {action} {symbol} "
            f"(confidence: {confidence:.2%}, "
            f"TP: {signal.get('tp_pct', 0):.2%}, "
            f"SL: {signal.get('sl_pct', 0):.2%})"
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
            eval_note: Evaluation score (-1 to 1, negative for short, positive for long)
            signal: Optional signal data for context
        """
        # Store signal data for the trading mode to access
        if signal:
            self.eval_note_time_to_live = self.signal_client.freshness_seconds
            
            # Set evaluation note
            await self.evaluation_completed(
                cryptocurrency=self.cryptocurrency,
                symbol=signal.get("symbol"),
                time_frame=None,
                eval_note=eval_note,
                eval_type=evaluator_enums.EvaluatorMatrixTypes.STRATEGIES
            )
    
    def get_signal_data(self) -> Optional[Dict[str, Any]]:
        """
        Get the most recent signal data.
        Used by the trading mode to access TP/SL levels.
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

