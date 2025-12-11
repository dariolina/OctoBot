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
External Signal Trading Mode

Trading mode that executes spot trades based on external signals.
Works in conjunction with ExternalSignalStrategyEvaluator.

Features:
- Executes buy trades based on signal action
- Applies 1% stop-loss (sells if price falls below entry - 1%)
- Automatically closes position at signal's close_time
- Respects signal freshness and bias thresholds
"""

import decimal
import asyncio
from typing import Optional, Dict, Any
from datetime import datetime, timezone

try:
    import octobot_trading.modes as trading_modes
    import octobot_trading.enums as trading_enums
    import octobot_trading.personal_data as trading_personal_data
    import octobot_evaluators.api as evaluators_api
    import octobot_evaluators.constants as evaluators_constants
    import octobot_evaluators.enums as evaluators_enums
    import octobot_evaluators.matrix as matrix
    import octobot_commons.constants as commons_constants
    import octobot_commons.evaluators_util as evaluators_util
except ImportError:
    # Handle case where trading package is not available
    class trading_modes:
        class AbstractTradingMode:
            pass

import octobot_commons.logging as logging


# TEMPORARY: Hardcoded fallback values for testing (remove after balance issue is fixed)
# These are used when portfolio and CCXT balance fetching both return 0
HARDCODED_BUY_FALLBACK_USDC = decimal.Decimal("1090")  # Fallback USDC balance for buy orders
HARDCODED_SELL_FALLBACK_BTC = decimal.Decimal("0.001")  # Fallback BTC balance for sell orders


# Define Producer and Consumer classes first (before trading mode class)
class ExternalSignalTradingModeProducer(trading_modes.AbstractTradingModeProducer):
    """
    Producer that reads strategy evaluations from the matrix and triggers consumer callbacks.
    Based on DailyTradingModeProducer pattern.
    """
    
    def __init__(self, channel, config, trading_mode, exchange_manager):
        super().__init__(channel, config, trading_mode, exchange_manager)
        self.final_eval = None
        self._last_submitted_eval = None
        self._last_submitted_key = None
        self._last_submit_time = 0
    
    async def set_final_eval(self, matrix_id: str, cryptocurrency: str, symbol: str, time_frame, trigger_source: str):
        """
        Called when strategy evaluations change in the matrix.
        Reads the evaluation and triggers consumer callback via submit_trading_evaluation.
        """
        self.logger.info(
            f"[PRODUCER] set_final_eval called: cryptocurrency={cryptocurrency}, symbol={symbol}, "
            f"time_frame={time_frame}, trigger_source={trigger_source}"
        )
        
        strategies_analysis_note_counter = 0
        evaluation = commons_constants.INIT_EVAL_NOTE
        
        # Read strategy evaluations from matrix
        for evaluated_strategy_node in matrix.get_tentacles_value_nodes(
                matrix_id,
                matrix.get_tentacle_nodes(matrix_id,
                                          exchange_name=self.exchange_name,
                                          tentacle_type=evaluators_enums.EvaluatorMatrixTypes.STRATEGIES.value),
                cryptocurrency=cryptocurrency,
                symbol=symbol):
            
            if evaluators_util.check_valid_eval_note(evaluators_api.get_value(evaluated_strategy_node),
                                                     evaluators_api.get_type(evaluated_strategy_node),
                                                     evaluators_constants.EVALUATOR_EVAL_DEFAULT_TYPE):
                eval_value = evaluators_api.get_value(evaluated_strategy_node)
                evaluation += eval_value
                strategies_analysis_note_counter += 1
                self.logger.debug(f"[PRODUCER] Found evaluation: {eval_value}")
        
        if strategies_analysis_note_counter > 0:
            self.final_eval = decimal.Decimal(str(evaluation / strategies_analysis_note_counter))
            self.logger.info(f"[PRODUCER] Final eval: {self.final_eval} (from {strategies_analysis_note_counter} strategies)")
            
            # Create a unique key for this evaluation
            eval_key = f"{cryptocurrency}:{symbol}:{self.final_eval}"
            current_time = asyncio.get_event_loop().time()
            
            # Debounce: skip if same evaluation was submitted recently (within 1 second)
            if (self._last_submitted_key == eval_key and 
                current_time - self._last_submit_time < 1.0):
                self.logger.debug(
                    f"[PRODUCER] Skipping duplicate evaluation submission: {eval_key} "
                    f"(last submitted {current_time - self._last_submit_time:.2f}s ago)"
                )
                return
            
            # Determine state from eval_note (positive = LONG, negative = SHORT, zero = NEUTRAL)
            if self.final_eval > 0:
                state = trading_enums.EvaluatorStates.LONG
            elif self.final_eval < 0:
                state = trading_enums.EvaluatorStates.SHORT
            else:
                state = trading_enums.EvaluatorStates.NEUTRAL
            
            # Trigger consumer callback via submit_trading_evaluation
            # This will call internal_callback() on all consumers
            await self.submit_trading_evaluation(
                cryptocurrency=cryptocurrency,
                symbol=symbol,
                time_frame=time_frame,
                final_note=self.final_eval,
                state=state,
                dependencies=None
            )
            
            # Track this submission to prevent duplicates
            self._last_submitted_eval = self.final_eval
            self._last_submitted_key = eval_key
            self._last_submit_time = current_time
        else:
            self.logger.debug(f"[PRODUCER] No valid strategy evaluations found in matrix")


class ExternalSignalTradingModeConsumer(trading_modes.AbstractTradingModeConsumer):
    """
    Consumer that processes external signals and creates spot trade orders.
    Manages stop-loss and time-based exits.
    """
    
    def __init__(self, trading_mode):
        super().__init__(trading_mode)
        self.logger = logging.get_logger(self.__class__.__name__)
        self._active_positions = {}  # Track active positions with their close tasks


class ExternalSignalTradingMode(trading_modes.AbstractTradingMode):
    """
    Trading mode that executes spot trades based on external signals.
    
    This mode:
    1. Receives evaluation signals from ExternalSignalStrategyEvaluator
    2. Checks if signal is actionable (fresh, valid action)
    3. Calculates position size
    4. Creates market buy orders
    5. Sets up 1% stop-loss
    6. Schedules automatic close at signal's close_time
    """
    
    def __init__(self, config, exchange_manager):
        super().__init__(config, exchange_manager)
        self.logger = logging.get_logger(self.__class__.__name__)
    
    def get_mode_producer_classes(self) -> list:
        """Return Producer classes for this trading mode."""
        return [ExternalSignalTradingModeProducer]
    
    def get_mode_consumer_classes(self) -> list:
        """Return Consumer classes for this trading mode."""
        return [ExternalSignalTradingModeConsumer]
    
    def init_user_inputs(self, inputs: dict) -> None:
        """
        Initialize user inputs for this trading mode.
        
        Available inputs:
        - position_size_percent: Percentage of portfolio to use per trade
        - min_bias: Minimum bias threshold (0.0 to 100.0)
        - stop_loss_percent: Stop loss percentage (default 1.0 = 1%)
        """
        self.position_size_percent = decimal.Decimal(
            str(inputs.get("position_size_percent", 10))
        )
        self.min_bias = decimal.Decimal(
            str(inputs.get("min_bias", 12.0))
        )
        self.stop_loss_percent = decimal.Decimal(
            str(inputs.get("stop_loss_percent", 1.0))
        )
        self.enable_stop_loss = inputs.get("enable_stop_loss", True)
        self.stop_loss_delay_seconds = inputs.get("stop_loss_delay_seconds", 5)
    
    @classmethod
    def get_supported_exchange_types(cls) -> list:
        """
        Returns supported exchange types.
        Supports spot trading only.
        """
        return [
            trading_enums.ExchangeTypes.SPOT,
        ]


class ExternalSignalTradingModeConsumer(trading_modes.AbstractTradingModeConsumer):
    """
    Consumer that processes external signals and creates spot trade orders.
    Manages stop-loss and time-based exits.
    """
    
    def __init__(self, trading_mode):
        super().__init__(trading_mode)
        self.logger = logging.get_logger(self.__class__.__name__)
        self._active_positions = {}  # Track active positions with their close tasks
        self._monitored_positions = {}  # Track positions for price monitoring: {symbol: {"entry_price": ..., "stop_loss_price": ..., "quantity": ...}}
        self._price_monitor_task = None  # Background task for price monitoring
        self._price_monitor_interval = 10  # Check price every 10 seconds
    
    async def internal_callback(self, trading_mode_name: str, cryptocurrency: str,
                               symbol: str, time_frame, final_note: float,
                               state, **kwargs):
        """
        Callback triggered when strategy evaluation is completed.
        
        Args:
            trading_mode_name: Name of the trading mode
            cryptocurrency: Cryptocurrency being traded
            symbol: Trading symbol (e.g., "BTC/USDC")
            time_frame: Time frame (not used by this strategy)
            final_note: Evaluation score from strategy (-1 to 1)
            state: Trading state
        """
        self.logger.info(
            f"[CALLBACK TRIGGERED] Trading mode callback received: "
            f"trading_mode_name={trading_mode_name}, cryptocurrency={cryptocurrency}, "
            f"symbol={symbol}, final_note={final_note}, time_frame={time_frame}, state={state}"
        )
        try:
            # Get signal data from strategy evaluator
            signal = self._get_signal_from_strategy()
            
            if not signal:
                self.logger.warning("[CONSUMER] No signal data available")
                return
            
            self.logger.info(f"[CONSUMER] Retrieved signal: {signal}")
            
            # Check if signal matches current symbol
            signal_pair = signal.get("pair", "").replace("-", "/")
            if signal_pair != symbol:
                self.logger.warning(
                    f"[CONSUMER] Signal pair {signal_pair} does not match {symbol}"
                )
                return
            
            # Check if this signal was already processed (via market_id)
            market_id = signal.get("market_id")
            if not self._is_signal_new(signal):
                self.logger.info(
                    f"[CONSUMER] Signal with market_id {market_id} already processed, skipping"
                )
                return
            
            # Check bias threshold
            bias = decimal.Decimal(str(signal.get("bias", 0)))
            if bias < self.trading_mode.min_bias:
                self.logger.info(
                    f"[CONSUMER] Signal bias {bias:.1f}% below threshold "
                    f"{self.trading_mode.min_bias:.1f}%"
                )
                return
            
            # Determine action from signal
            action = signal.get("action")
            self.logger.info(f"[CONSUMER] Processing {action} action for {symbol}")
            
            # Execute trade based on action
            if action == "buy":
                await self._execute_buy_trade(symbol, signal)
            elif action == "sell":
                await self._execute_sell_trade(symbol, signal)
            else:
                self.logger.warning(f"[CONSUMER] No action for signal: {action}")
        
        except Exception as e:
            self.logger.exception(e, True, f"Error processing signal: {e}")
    
    def _get_signal_from_strategy(self) -> Optional[Dict[str, Any]]:
        """Retrieve signal data from the strategy evaluator."""
        try:
            # Access signal client directly from config (same singleton instance used by strategy evaluator)
            from octobot.utils import signal_client
            
            # Get config from exchange manager
            config = self.exchange_manager.config if hasattr(self.exchange_manager, 'config') else self.trading_mode.config
            client = signal_client.create_signal_client_from_config(config)
            
            if client:
                signal = client.get_cached_signal()
                if signal:
                    self.logger.debug(f"[CONSUMER] Retrieved cached signal: {signal.get('market_id')}")
                    return signal
                else:
                    self.logger.debug("[CONSUMER] No cached signal available (may be stale or not fetched yet)")
            else:
                self.logger.warning("[CONSUMER] Signal client not available (external_signal not enabled?)")
            
            return None
        except Exception as e:
            self.logger.exception(e, True, f"[CONSUMER] Error getting signal from strategy: {e}")
            return None
    
    def _is_signal_new(self, signal: Dict[str, Any]) -> bool:
        """
        Check if signal has not been processed yet based on market_id.
        
        Args:
            signal: Signal dict to check
            
        Returns:
            True if signal is new (not yet processed)
        """
        try:
            # Access signal client from trading mode's strategy
            from octobot.utils import signal_client
            config = self.trading_mode.config
            client = signal_client.create_signal_client_from_config(config)
            
            if client:
                return client.is_signal_new(signal)
            
            # If no client, assume signal is new
            return True
        except Exception as e:
            self.logger.debug(f"Error checking if signal is new: {e}")
            return True  # On error, assume new to avoid blocking trades
    
    def _mark_signal_processed(self, signal: Dict[str, Any]) -> None:
        """
        Mark signal as processed to prevent duplicate trades.
        
        Args:
            signal: Signal dict that was processed
        """
        try:
            from octobot.utils import signal_client
            config = self.trading_mode.config
            client = signal_client.create_signal_client_from_config(config)
            
            if client:
                market_id = signal.get("market_id")
                if market_id:
                    client.mark_signal_processed(market_id)
        except Exception as e:
            self.logger.error(f"Error marking signal as processed: {e}")
    
    async def _schedule_position_close(self, symbol: str, close_time_str: str, quantity: decimal.Decimal):
        """
        Schedule automatic position close at the specified time.
        Whichever comes first: stop-loss or close_time.
        
        Args:
            symbol: Trading symbol
            close_time_str: ISO timestamp string
            quantity: Position quantity
        """
        try:
            close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            
            if close_time <= now:
                self.logger.warning(f"Close time {close_time_str} is in the past, closing immediately")
                await self._close_position_at_market(symbol, quantity=None)
                return
            
            wait_seconds = (close_time - now).total_seconds()
            self.logger.info(
                f"Scheduled position close for {symbol} at {close_time_str} "
                f"({wait_seconds:.0f}s from now)"
            )
            
            # Create and store the close task
            task = asyncio.create_task(self._wait_and_close_position(symbol, wait_seconds, quantity))
            self._active_positions[symbol] = task
            
        except (ValueError, TypeError) as e:
            self.logger.error(f"Invalid close_time format: {close_time_str}, error: {e}")
    
    async def _wait_and_close_position(self, symbol: str, wait_seconds: float, quantity: decimal.Decimal):
        """
        Wait for the specified duration and then close the position.
        
        Args:
            symbol: Trading symbol
            wait_seconds: Seconds to wait
            quantity: Position quantity (used as fallback, actual position is checked)
        """
        try:
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            else:
                self.logger.warning(f"Close time for {symbol} is in the past, closing immediately")
            
            self.logger.info(f"Close time reached for {symbol}, closing position")
            # Pass None to use actual position quantity
            await self._close_position_at_market(symbol, quantity=None)
            
            # Remove from active positions
            if symbol in self._active_positions:
                del self._active_positions[symbol]
                
        except asyncio.CancelledError:
            self.logger.info(f"Position close task cancelled for {symbol}")
        except Exception as e:
            self.logger.exception(e, True, f"Error closing position at scheduled time: {e}")
            # Still try to remove from active positions
            if symbol in self._active_positions:
                del self._active_positions[symbol]
    
    async def _price_monitoring_loop(self):
        """
        Background task that monitors positions and triggers stop-loss if price falls below threshold.
        Checks price every _price_monitor_interval seconds.
        """
        self.logger.info("Price monitoring loop started")
        while True:
            try:
                await asyncio.sleep(self._price_monitor_interval)
                
                if not self._monitored_positions:
                    continue
                
                # Check each monitored position
                for symbol, position_info in list(self._monitored_positions.items()):
                    try:
                        current_price = await self._get_current_price(symbol)
                        if not current_price:
                            continue
                        
                        entry_price = position_info["entry_price"]
                        stop_loss_price = position_info["stop_loss_price"]
                        
                        # Check if price has fallen below stop-loss threshold
                        if current_price <= stop_loss_price:
                            self.logger.warning(
                                f"Price monitoring: {symbol} price {current_price} fell below stop-loss "
                                f"{stop_loss_price} (entry: {entry_price}). Triggering sell."
                            )
                            
                            # Trigger stop-loss sell
                            await self._close_position_at_market(symbol, quantity=None)
                            
                            # Remove from monitoring
                            del self._monitored_positions[symbol]
                            
                            # Cancel scheduled close task if exists
                            if symbol in self._active_positions:
                                task = self._active_positions[symbol]
                                if not task.done():
                                    task.cancel()
                                del self._active_positions[symbol]
                    except Exception as e:
                        self.logger.warning(f"Error monitoring price for {symbol}: {e}")
                        continue
                        
            except asyncio.CancelledError:
                self.logger.info("Price monitoring loop cancelled")
                break
            except Exception as e:
                self.logger.exception(e, True, f"Error in price monitoring loop: {e}")
                await asyncio.sleep(self._price_monitor_interval)
    
    async def _close_position_at_market(self, symbol: str, quantity: Optional[decimal.Decimal] = None):
        """
        Close position by creating a market sell order.
        Also cancels any open stop-loss orders for this symbol.
        
        Args:
            symbol: Trading symbol
            quantity: Quantity to sell (if None, uses available balance)
        """
        try:
            # Get actual position quantity from portfolio
            base_currency = symbol.split("/")[0]
            portfolio = self.exchange_manager.exchange_personal_data.portfolio_manager.portfolio
            available = portfolio.get_currency_portfolio(base_currency).available
            
            if quantity is None:
                quantity = available
            
            if quantity <= 0:
                self.logger.warning(f"No {base_currency} available to sell for {symbol}")
                # Still try to cancel stop-loss orders
                await self._cancel_stop_loss_orders(symbol)
                return
            
            # Cancel any open stop-loss orders first
            await self._cancel_stop_loss_orders(symbol)
            
            current_price = await self._get_current_price(symbol)
            if not current_price:
                self.logger.error(f"Could not get current price for {symbol}")
                return
            
            await self._create_order(
                symbol=symbol,
                order_type=trading_enums.TraderOrderType.SELL_MARKET,
                quantity=quantity,
                price=current_price
            )
            
            self.logger.info(f"Position closed at market: {symbol} qty={quantity} price={current_price}")
            
            # Remove from price monitoring
            if symbol in self._monitored_positions:
                del self._monitored_positions[symbol]
            
        except Exception as e:
            self.logger.exception(e, True, f"Error closing position: {e}")
    
    async def _cancel_stop_loss_orders(self, symbol: str):
        """Cancel any open stop-loss orders for the given symbol."""
        try:
            import octobot_trading.api as trading_api
            open_orders = trading_api.get_open_orders(self.exchange_manager, symbol=symbol)
            
            for order in open_orders:
                if order.order_type == trading_enums.TraderOrderType.STOP_LOSS:
                    self.logger.info(f"Cancelling stop-loss order for {symbol}: {order.order_id}")
                    await self.exchange_manager.trader.cancel_order(order)
        except Exception as e:
            self.logger.warning(f"Error cancelling stop-loss orders for {symbol}: {e}")
    
    async def _execute_buy_trade(self, symbol: str, signal: Dict[str, Any]):
        """
        Execute a buy trade based on signal.
        Buys the base currency (e.g., BTC) with quote currency (e.g., USDC).
        Sets up 1% stop-loss and schedules close at close_time.
        
        Args:
            symbol: Trading symbol (e.g., "BTC/USDC")
            signal: Signal dict containing bias and close_time
        """
        self.logger.info(f"Executing BUY trade for {symbol}")
        
        try:
            # Validate close_time is at least 10 minutes away
            close_time_str = signal.get("close_time")
            if close_time_str:
                try:
                    close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    time_until_close = (close_time - now).total_seconds()
                    min_time_seconds = 600  # 10 minutes
                    
                    if time_until_close < min_time_seconds:
                        self.logger.warning(
                            f"Rejecting buy signal for {symbol}: close_time ({close_time_str}) "
                            f"is only {time_until_close:.0f}s away (minimum {min_time_seconds}s required)"
                        )
                        return
                except (ValueError, TypeError) as e:
                    self.logger.warning(
                        f"Invalid close_time format in signal: {close_time_str}, error: {e}. "
                        f"Proceeding with trade (validation skipped)."
                    )
            
            # Get current price
            current_price = await self._get_current_price(symbol)
            if not current_price:
                self.logger.error(f"Could not get current price for {symbol}")
                return
            
            # Calculate position size
            quantity = await self._calculate_position_size(
                symbol, current_price, trading_enums.TradeOrderSide.BUY
            )
            
            if quantity <= 0:
                self.logger.warning(f"Insufficient funds for buy trade on {symbol}")
                return
            
            # Create market buy order first (without stop-loss)
            # Use same pattern as DailyTradingMode: check if order was returned
            try:
                buy_order = await self._create_order(
                symbol=symbol,
                order_type=trading_enums.TraderOrderType.BUY_MARKET,
                quantity=quantity,
                price=current_price,
                    stop_loss_price=None  # Don't place stop-loss immediately
                )
            except Exception as e:
                self.logger.error(
                    f"Exception creating buy order for {symbol}: {e}. "
                    f"Not proceeding with stop-loss, monitoring, or marking signal as processed."
                )
                return
            
            # Check if order was actually created successfully (same pattern as DailyTradingMode)
            if not buy_order:
                self.logger.error(
                    f"Buy order creation returned None for {symbol}. "
                    f"Not proceeding with stop-loss, monitoring, or marking signal as processed."
                )
                return
            
            # If order was returned, it was submitted successfully
            # Note: exchange_order_id may be set asynchronously by OctoBot, but order was created
            self.logger.info(
                f"Buy order successfully created: {symbol} qty={quantity} "
                f"entry={current_price} "
                f"order_id={buy_order.exchange_order_id if hasattr(buy_order, 'exchange_order_id') and buy_order.exchange_order_id else 'pending'}"
            )
            
            # Calculate stop-loss price (1% below entry) if enabled
            sl_price = None
            if self.trading_mode.enable_stop_loss:
                sl_pct = self.trading_mode.stop_loss_percent / decimal.Decimal("100")
                sl_price = current_price * (decimal.Decimal("1") - sl_pct)
                
                # Wait a bit before placing stop-loss to avoid immediate trigger
                if self.trading_mode.stop_loss_delay_seconds > 0:
                    self.logger.info(
                        f"Waiting {self.trading_mode.stop_loss_delay_seconds}s before placing stop-loss "
                        f"for {symbol} at {sl_price}"
                    )
                    await asyncio.sleep(self.trading_mode.stop_loss_delay_seconds)
                
                # Place stop-loss order after delay
                try:
                    await self._create_stop_loss_order(symbol, quantity, current_price, sl_price)
                except Exception as e:
                    self.logger.warning(
                        f"Failed to place stop-loss order for {symbol} (exchange may not support it): {e}"
            )
            
            self.logger.info(
                f"Buy order completed: {symbol} qty={quantity} "
                f"entry={current_price} "
                f"{'SL=' + str(sl_price) if sl_price else '(stop-loss disabled)'} "
                f"market_id={signal.get('market_id')}"
            )
            
            # Mark signal as processed to prevent duplicate trades (only after successful buy)
            self._mark_signal_processed(signal)
            
            # Add to price monitoring (for on-chain price monitoring)
            self._monitored_positions[symbol] = {
                "entry_price": current_price,
                "stop_loss_price": sl_price if sl_price else current_price * decimal.Decimal("0.98"),  # Default 2% stop-loss
                "quantity": quantity,
                "entry_time": datetime.now(timezone.utc)
            }
            
            # Start price monitoring task if not already running
            if self._price_monitor_task is None or self._price_monitor_task.done():
                self._price_monitor_task = asyncio.create_task(self._price_monitoring_loop())
                self.logger.info("Started price monitoring task")
            
            # Schedule automatic close at close_time
            close_time_str = signal.get("close_time")
            if close_time_str:
                await self._schedule_position_close(symbol, close_time_str, quantity)
        
        except Exception as e:
            self.logger.exception(e, True, f"Error executing buy trade: {e}")
    
    async def _execute_sell_trade(self, symbol: str, signal: Dict[str, Any]):
        """
        Execute a sell trade based on signal.
        Sells existing position in the base currency.
        
        Args:
            symbol: Trading symbol
            signal: Signal dict
        """
        self.logger.info(f"Executing SELL trade for {symbol}")
        
        try:
            # Get current price
            current_price = await self._get_current_price(symbol)
            if not current_price:
                self.logger.error(f"Could not get current price for {symbol}")
                return
            
            # Get available balance to sell
            base_currency = symbol.split("/")[0]
            portfolio = self.exchange_manager.exchange_personal_data.portfolio_manager.portfolio
            currency_portfolio = portfolio.get_currency_portfolio(base_currency)
            available = currency_portfolio.available
            total = currency_portfolio.total
            
            self.logger.info(
                f"[SELL] {base_currency} balance: available={available}, total={total}"
            )
            
            # Workaround for Hyperliquid spot balance issue: try fetching balance directly from CCXT
            if available == 0 and total == 0:
                self.logger.warning(
                    f"[SELL] Portfolio shows 0 balance for {base_currency}. "
                    f"Attempting to fetch balance directly from exchange..."
                )
                try:
                    # Try to fetch balance directly from CCXT exchange connector
                    exchange_connector = self.exchange_manager.exchange.connector
                    if hasattr(exchange_connector, 'client') and hasattr(exchange_connector.client, 'fetch_balance'):
                        raw_balance = await exchange_connector.client.fetch_balance()
                        self.logger.info(f"[SELL] Raw balance from CCXT: {raw_balance}")
                        
                        # Extract balance for the base currency
                        if base_currency in raw_balance:
                            currency_balance = raw_balance[base_currency]
                            if isinstance(currency_balance, dict):
                                ccxt_available = decimal.Decimal(str(currency_balance.get('free', 0)))
                                ccxt_total = decimal.Decimal(str(currency_balance.get('total', 0)))
                            else:
                                # Handle case where balance might be a number
                                ccxt_available = decimal.Decimal(str(currency_balance))
                                ccxt_total = ccxt_available
                            
                            self.logger.info(
                                f"[SELL] CCXT balance for {base_currency}: "
                                f"free={ccxt_available}, total={ccxt_total}"
                            )
                            
                            if ccxt_available > 0 or ccxt_total > 0:
                                self.logger.info(
                                    f"[SELL] Using CCXT balance instead of portfolio balance"
                                )
                                available = ccxt_available
                                total = ccxt_total
                except Exception as e:
                    self.logger.warning(
                        f"[SELL] Failed to fetch balance directly from CCXT: {e}"
                    )
                
                # TEMPORARY: Fallback to buy quantity from latest order if available
                # This uses the quantity we tracked when we executed the buy order
                if available == 0 and total == 0:
                    if symbol in self._monitored_positions:
                        tracked_quantity = self._monitored_positions[symbol].get("quantity")
                        if tracked_quantity and tracked_quantity > 0:
                            self.logger.warning(
                                f"[SELL] Using tracked buy quantity from latest order: {tracked_quantity} {base_currency}"
                            )
                            available = tracked_quantity
                            total = tracked_quantity
                        else:
                            # Fallback to hardcoded value if no tracked quantity
                            if base_currency == "BTC":
                                self.logger.warning(
                                    f"[SELL] No tracked quantity found, using hardcoded test position size: {HARDCODED_SELL_FALLBACK_BTC} BTC"
                                )
                                available = HARDCODED_SELL_FALLBACK_BTC
                                total = HARDCODED_SELL_FALLBACK_BTC
                    else:
                        # Fallback to hardcoded value if no tracked position
                        if base_currency == "BTC":
                            self.logger.warning(
                                f"[SELL] No tracked position found, using hardcoded test position size: {HARDCODED_SELL_FALLBACK_BTC} BTC"
                            )
                            available = HARDCODED_SELL_FALLBACK_BTC
                            total = HARDCODED_SELL_FALLBACK_BTC
            
            if available <= 0:
                self.logger.warning(f"No {base_currency} balance to sell")
                return
            
            # Round to exchange precision
            quantity = self._round_to_exchange_precision(symbol, available)
            
            if quantity <= 0:
                self.logger.warning(f"Insufficient {base_currency} to sell")
                return
            
            # Create market sell order
            await self._create_order(
                symbol=symbol,
                order_type=trading_enums.TraderOrderType.SELL_MARKET,
                quantity=quantity,
                price=current_price
            )
            
            self.logger.info(
                f"Sell order created: {symbol} qty={quantity} price={current_price} "
                f"market_id={signal.get('market_id')}"
            )
            
            # Mark signal as processed to prevent duplicate trades
            self._mark_signal_processed(signal)
            
            # Cancel any scheduled close task for this symbol
            if symbol in self._active_positions:
                task = self._active_positions[symbol]
                if not task.done():
                    task.cancel()
                del self._active_positions[symbol]
        
        except Exception as e:
            self.logger.exception(e, True, f"Error executing sell trade: {e}")
    
    async def _get_current_price(self, symbol: str) -> Optional[decimal.Decimal]:
        """Get current market price for symbol."""
        try:
            ticker = await self.exchange_manager.exchange.get_price_ticker(symbol)
            if ticker:
                # Try different price fields (last, close, bid, ask, markPrice)
                price_value = None
                for price_key in ["last", "close", "bid", "ask", "markPrice"]:
                    if price_key in ticker and ticker[price_key] is not None:
                        try:
                            price_value = ticker[price_key]
                            # Convert to string first, then Decimal
                            if isinstance(price_value, (int, float)):
                                price_value = str(price_value)
                            elif not isinstance(price_value, str):
                                price_value = str(price_value)
                            return decimal.Decimal(price_value)
                        except (ValueError, decimal.InvalidOperation, decimal.ConversionSyntax) as e:
                            self.logger.debug(f"Could not convert {price_key} price {price_value} to Decimal: {e}")
                            continue
                
                # Try info.markPx (Hyperliquid uses this)
                if "info" in ticker and isinstance(ticker["info"], dict):
                    info = ticker["info"]
                    for mark_key in ["markPx", "markPrice", "midPx"]:
                        if mark_key in info and info[mark_key] is not None:
                            try:
                                price_value = info[mark_key]
                                if isinstance(price_value, (int, float)):
                                    price_value = str(price_value)
                                elif not isinstance(price_value, str):
                                    price_value = str(price_value)
                                return decimal.Decimal(price_value)
                            except (ValueError, decimal.InvalidOperation, decimal.ConversionSyntax) as e:
                                self.logger.debug(f"Could not convert info.{mark_key} price {price_value} to Decimal: {e}")
                                continue
                
                self.logger.warning(f"No valid price found in ticker for {symbol}: {ticker}")
            else:
                self.logger.warning(f"No ticker data returned for {symbol}")
        except Exception as e:
            self.logger.exception(e, True, f"Error getting price for {symbol}: {e}")
        
        return None
    
    async def _calculate_position_size(
        self, symbol: str, price: decimal.Decimal, side: trading_enums.TradeOrderSide
    ) -> decimal.Decimal:
        """
        Calculate position size based on portfolio percentage.
        
        Args:
            symbol: Trading symbol
            price: Current price
            side: Order side (BUY or SELL)
            
        Returns:
            Quantity to trade
        """
        try:
            portfolio = self.exchange_manager.exchange_personal_data.portfolio_manager.portfolio
            
            # Parse symbol
            base_currency = symbol.split("/")[0]
            quote_currency = symbol.split("/")[1]
            
            # Get available balance
            if side == trading_enums.TradeOrderSide.BUY:
                # For buy, use quote currency (e.g., USDT in BTC/USDT)
                currency_portfolio = portfolio.get_currency_portfolio(quote_currency)
                available = currency_portfolio.available
                total = currency_portfolio.total
                currency_name = quote_currency
                self.logger.info(
                    f"[POSITION SIZE] {quote_currency} balance: available={available}, total={total}"
                )
            else:
                # For sell, use base currency (e.g., BTC in BTC/USDT)
                currency_portfolio = portfolio.get_currency_portfolio(base_currency)
                available = currency_portfolio.available
                total = currency_portfolio.total
                currency_name = base_currency
                self.logger.info(
                    f"[POSITION SIZE] {base_currency} balance: available={available}, total={total}"
                )
            
            # Workaround for Hyperliquid spot balance issue: try fetching balance directly from CCXT
            if available == 0 and total == 0:
                self.logger.warning(
                    f"[POSITION SIZE] Portfolio shows 0 balance for {currency_name}. "
                    f"Attempting to fetch balance directly from exchange..."
                )
                try:
                    # Try to fetch balance directly from CCXT exchange connector
                    exchange_connector = self.exchange_manager.exchange.connector
                    if hasattr(exchange_connector, 'client') and hasattr(exchange_connector.client, 'fetch_balance'):
                        raw_balance = await exchange_connector.client.fetch_balance()
                        self.logger.info(f"[POSITION SIZE] Raw balance from CCXT: {raw_balance}")
                        
                        # Extract balance for the currency we need
                        if currency_name in raw_balance:
                            currency_balance = raw_balance[currency_name]
                            if isinstance(currency_balance, dict):
                                ccxt_available = decimal.Decimal(str(currency_balance.get('free', 0)))
                                ccxt_total = decimal.Decimal(str(currency_balance.get('total', 0)))
                            else:
                                # Handle case where balance might be a number
                                ccxt_available = decimal.Decimal(str(currency_balance))
                                ccxt_total = ccxt_available
                            
                            self.logger.info(
                                f"[POSITION SIZE] CCXT balance for {currency_name}: "
                                f"free={ccxt_available}, total={ccxt_total}"
                            )
                            
                            if ccxt_available > 0 or ccxt_total > 0:
                                self.logger.info(
                                    f"[POSITION SIZE] Using CCXT balance instead of portfolio balance"
                                )
                                available = ccxt_available
                                total = ccxt_total
                except Exception as e:
                    self.logger.warning(
                        f"[POSITION SIZE] Failed to fetch balance directly from CCXT: {e}"
                    )
                
                # TEMPORARY: Hardcoded fallback for testing (remove after balance issue is fixed)
                # Using HARDCODED_BUY_FALLBACK_USDC so 10% meets Hyperliquid minimum order value
                if available == 0 and total == 0 and side == trading_enums.TradeOrderSide.BUY and quote_currency == "USDC":
                    self.logger.warning(
                        f"[POSITION SIZE] Using hardcoded test position size: {HARDCODED_BUY_FALLBACK_USDC} USDC"
                    )
                    available = HARDCODED_BUY_FALLBACK_USDC
                    total = HARDCODED_BUY_FALLBACK_USDC
            
            # Calculate quantity based on position size percentage
            position_size_pct = self.trading_mode.position_size_percent
            position_value = available * (position_size_pct / decimal.Decimal("100"))
            self.logger.info(
                f"[POSITION SIZE] Using {position_size_pct}% of {available} = {position_value} {currency_name}"
            )
            
            if side == trading_enums.TradeOrderSide.BUY:
                quantity = position_value / price
                self.logger.info(
                    f"[POSITION SIZE] Calculated quantity: {position_value} / {price} = {quantity} {symbol.split('/')[0]}"
                )
            else:
                quantity = position_value
                self.logger.info(
                    f"[POSITION SIZE] Calculated quantity: {quantity} {base_currency}"
                )
            
            # Round to exchange precision
            quantity = self._round_to_exchange_precision(symbol, quantity)
            
            self.logger.info(
                f"[POSITION SIZE] Final quantity after rounding: {quantity}"
            )
            
            return quantity
        
        except Exception as e:
            self.logger.exception(e, True, f"Error calculating position size: {e}")
            return decimal.Decimal("0")
    
    def _round_to_exchange_precision(
        self, symbol: str, quantity: decimal.Decimal
    ) -> decimal.Decimal:
        """Round quantity to exchange precision requirements."""
        try:
            symbol_market = self.exchange_manager.exchange.get_market_status(symbol)
            if symbol_market and "limits" in symbol_market:
                min_amount = symbol_market["limits"].get("amount", {}).get("min", 0)
                if quantity < decimal.Decimal(str(min_amount)):
                    return decimal.Decimal("0")
            
            # Use exchange precision
            precision = symbol_market.get("precision", {}).get("amount", 8)
            return quantity.quantize(decimal.Decimal(f"1e-{precision}"))
        except Exception:
            return quantity
    
    async def _create_order(
        self,
        symbol: str,
        order_type: trading_enums.TraderOrderType,
        quantity: decimal.Decimal,
        price: decimal.Decimal,
        stop_loss_price: Optional[decimal.Decimal] = None
    ):
        """
        Create an order with optional stop-loss.
        
        Args:
            symbol: Trading symbol
            order_type: Type of order
            quantity: Order quantity
            price: Order price
            stop_loss_price: Stop-loss price
            
        Returns:
            Created order object, or None if creation failed
        """
        try:
            # Create main order
            order = trading_personal_data.create_order_instance(
                trader=self.exchange_manager.trader,
                order_type=order_type,
                symbol=symbol,
                current_price=price,
                quantity=quantity,
                price=price
            )
            
            # Submit order
            await self.exchange_manager.trader.create_order(order)
        
            # Return the order if successfully created
            return order
            
        except Exception as e:
            self.logger.error(f"Failed to create {order_type} order for {symbol}: {e}")
            return None
        
        # Note: Stop-loss orders are now created separately with delay
        # See _create_stop_loss_order() method
    
    async def _create_stop_loss_order(
        self,
        symbol: str,
        quantity: decimal.Decimal,
        current_price: decimal.Decimal,
        stop_loss_price: decimal.Decimal
    ):
        """
        Create a stop-loss order separately (called after buy order with delay).
        
        Args:
            symbol: Trading symbol
            quantity: Order quantity
            current_price: Current market price
            stop_loss_price: Stop-loss trigger price
        """
        try:
            sl_order_type = trading_enums.TraderOrderType.STOP_LOSS
            
            sl_order = trading_personal_data.create_order_instance(
                trader=self.exchange_manager.trader,
                order_type=sl_order_type,
                symbol=symbol,
                current_price=current_price,
                quantity=quantity,
                price=stop_loss_price
            )
            
            await self.exchange_manager.trader.create_order(sl_order)
            self.logger.info(f"Stop-loss order placed for {symbol} at {stop_loss_price}")
        except Exception as e:
            self.logger.warning(
                f"Could not place stop-loss order on exchange for {symbol}. "
                f"This may be normal if the exchange doesn't support stop-loss for spot. Error: {e}"
            )
            # Don't raise - allow trading to continue without stop-loss


# For compatibility with tentacles system
def get_trading_mode_class():
    """Return trading mode class for tentacles registration."""
    return ExternalSignalTradingMode

