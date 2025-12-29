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
except ImportError:
    # Handle case where trading package is not available
    class trading_modes:
        class AbstractTradingMode:
            pass

import octobot_commons.logging as logging


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
    
    MODE_PRODUCER_CLASSES = []
    MODE_CONSUMER_CLASSES = []
    
    def __init__(self, config, exchange_manager):
        super().__init__(config, exchange_manager)
        self.logger = logging.get_logger(self.__class__.__name__)
    
    def init_user_inputs(self, inputs: dict) -> None:
        """
        Initialize user inputs for this trading mode.
        
        Available inputs:
        - position_size_percent: Percentage of portfolio to use per trade
        - min_bias: Minimum bias threshold (0.0 to 100.0)
        - stop_loss_percent: Stop loss percentage (default 1.0 = 1%)
        - leverage: Leverage multiplier for perps trading (default: 1, no leverage)
        - margin_mode: Margin mode for perps ("cross" or "isolated", default: "cross")
        - enable_shorting: Allow short positions for perps (default: false)
        """
        self.position_size_percent = decimal.Decimal(
            str(inputs.get("position_size_percent", 10))
        )
        self.min_bias = decimal.Decimal(
            str(inputs.get("min_bias", 50.0))
        )
        self.stop_loss_percent = decimal.Decimal(
            str(inputs.get("stop_loss_percent", 1.0))
        )
        # Perps-specific configuration
        self.leverage = decimal.Decimal(
            str(inputs.get("leverage", 1))
        )
        self.margin_mode = inputs.get("margin_mode", "cross")
        self.enable_shorting = inputs.get("enable_shorting", False)
    
    @classmethod
    def get_supported_exchange_types(cls) -> list:
        """
        Returns supported exchange types.
        Supports both spot and futures/perpetuals trading.
        """
        return [
            trading_enums.ExchangeTypes.SPOT,
            trading_enums.ExchangeTypes.FUTURE,
        ]
    
    async def create_producers(self) -> list:
        """Create trading mode producers (if any)."""
        return []
    
    async def create_consumers(self) -> list:
        """Create trading mode consumers that react to signals."""
        consumers = []
        
        # Create consumer for each symbol
        for symbol in self.exchange_manager.exchange_config.traded_symbol_pairs:
            consumer = ExternalSignalTradingModeConsumer(self)
            await consumer.initialize()
            consumers.append(consumer)
        
        return consumers


class ExternalSignalTradingModeConsumer(trading_modes.AbstractTradingModeConsumer):
    """
    Consumer that processes external signals and creates spot trade orders.
    Manages stop-loss and time-based exits.
    """
    
    def __init__(self, trading_mode):
        super().__init__(trading_mode)
        self.logger = logging.get_logger(self.__class__.__name__)
        self._active_positions = {}  # Track active positions with their close tasks
    
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
        try:
            # Get signal data from strategy evaluator
            signal = self._get_signal_from_strategy()
            
            if not signal:
                self.logger.debug("No signal data available")
                return
            
            # Check if signal matches current symbol
            signal_pair = signal.get("pair", "").replace("-", "/")
            if signal_pair != symbol:
                self.logger.debug(
                    f"Signal pair {signal_pair} does not match {symbol}"
                )
                return
            
            # Check if this signal was already processed (via market_id)
            market_id = signal.get("market_id")
            if not self._is_signal_new(signal):
                self.logger.debug(
                    f"Signal with market_id {market_id} already processed, skipping"
                )
                return
            
            # Check bias threshold
            bias = decimal.Decimal(str(signal.get("bias", 0)))
            if bias < self.trading_mode.min_bias:
                self.logger.info(
                    f"Signal bias {bias:.1f}% below threshold "
                    f"{self.trading_mode.min_bias:.1f}%"
                )
                return
            
            # Determine action from signal
            action = signal.get("action")
            
            # Execute trade based on action
            if action == "buy":
                await self._execute_buy_trade(symbol, signal)
            elif action == "sell":
                await self._execute_sell_trade(symbol, signal)
            else:
                self.logger.debug(f"No action for signal: {action}")
        
        except Exception as e:
            self.logger.exception(e, True, f"Error processing signal: {e}")
    
    def _get_signal_from_strategy(self) -> Optional[Dict[str, Any]]:
        """Retrieve signal data from the strategy evaluator."""
        try:
            from octobot.external_signals.strategy_evaluator import ExternalSignalStrategyEvaluator
            
            # Access strategy evaluator from evaluator channels
            # This is a simplified approach - in production, use proper channel communication
            # For now, we can access through the trading mode's exchange manager
            
            return None  # Placeholder - implement based on OctoBot's architecture
        except Exception as e:
            self.logger.debug(f"Could not get signal from strategy: {e}")
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
                await self._close_position_at_market(symbol, quantity)
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
            quantity: Position quantity
        """
        try:
            await asyncio.sleep(wait_seconds)
            self.logger.info(f"Close time reached for {symbol}, closing position")
            await self._close_position_at_market(symbol, quantity)
            
            # Remove from active positions
            if symbol in self._active_positions:
                del self._active_positions[symbol]
                
        except asyncio.CancelledError:
            self.logger.info(f"Position close task cancelled for {symbol}")
        except Exception as e:
            self.logger.exception(e, True, f"Error closing position at scheduled time: {e}")
    
    async def _close_position_at_market(self, symbol: str, quantity: decimal.Decimal):
        """
        Close position by creating a market sell order.
        
        Args:
            symbol: Trading symbol
            quantity: Quantity to sell
        """
        try:
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
            
        except Exception as e:
            self.logger.exception(e, True, f"Error closing position: {e}")
    
    async def _execute_buy_trade(self, symbol: str, signal: Dict[str, Any]):
        """
        Execute a buy trade based on signal.
        For spot: Buys the base currency (e.g., BTC) with quote currency (e.g., USDC).
        For perps: Opens a long position.
        Sets up stop-loss and schedules close at close_time.
        
        Args:
            symbol: Trading symbol (e.g., "BTC/USDC")
            signal: Signal dict containing bias and close_time
        """
        is_perps = self._is_perps_exchange()
        action_type = "LONG position" if is_perps else "BUY"
        self.logger.info(f"Executing {action_type} trade for {symbol}")
        
        try:
            # Get current price
            current_price = await self._get_current_price(symbol)
            if not current_price:
                self.logger.error(f"Could not get current price for {symbol}")
                return
            
            # For perps: set leverage and margin mode before opening position
            if is_perps:
                await self._setup_perps_settings(symbol)
            
            # Calculate position size
            quantity = await self._calculate_position_size(
                symbol, current_price, trading_enums.TradeOrderSide.BUY
            )
            
            if quantity <= 0:
                self.logger.warning(f"Insufficient funds for {action_type.lower()} trade on {symbol}")
                return
            
            # Check for existing positions (perps only)
            if is_perps:
                existing_position = await self._get_existing_position(symbol)
                if existing_position:
                    position_size = decimal.Decimal(str(existing_position.get("size", 0)))
                    if position_size > 0:
                        self.logger.info(
                            f"Existing long position found: {symbol} size={position_size}. "
                            f"Will add to position."
                        )
            
            # Calculate stop-loss price (1% below entry)
            sl_pct = self.trading_mode.stop_loss_percent / decimal.Decimal("100")
            sl_price = current_price * (decimal.Decimal("1") - sl_pct)
            
            # Create market buy order
            await self._create_order(
                symbol=symbol,
                order_type=trading_enums.TraderOrderType.BUY_MARKET,
                quantity=quantity,
                price=current_price,
                stop_loss_price=sl_price,
                is_perps=is_perps
            )
            
            self.logger.info(
                f"{action_type} order created: {symbol} qty={quantity} "
                f"entry={current_price} SL={sl_price} market_id={signal.get('market_id')}"
            )
            
            # Mark signal as processed to prevent duplicate trades
            self._mark_signal_processed(signal)
            
            # Schedule automatic close at close_time
            close_time_str = signal.get("close_time")
            if close_time_str:
                await self._schedule_position_close(symbol, close_time_str, quantity)
        
        except Exception as e:
            self.logger.exception(e, True, f"Error executing {action_type.lower()} trade: {e}")
    
    async def _execute_sell_trade(self, symbol: str, signal: Dict[str, Any]):
        """
        Execute a sell trade based on signal.
        For spot: Sells existing position in the base currency.
        For perps: Closes long position or opens short position (if enabled).
        
        Args:
            symbol: Trading symbol
            signal: Signal dict
        """
        is_perps = self._is_perps_exchange()
        self.logger.info(f"Executing SELL trade for {symbol} ({'perps' if is_perps else 'spot'})")
        
        try:
            # Get current price
            current_price = await self._get_current_price(symbol)
            if not current_price:
                self.logger.error(f"Could not get current price for {symbol}")
                return
            
            if is_perps:
                # For perps: check existing position
                existing_position = await self._get_existing_position(symbol)
                if existing_position:
                    position_size = decimal.Decimal(str(existing_position.get("size", 0)))
                    if position_size > 0:
                        # Close long position
                        self.logger.info(f"Closing long position: {symbol} size={position_size}")
                        quantity = position_size
                    else:
                        # No position to close, check if shorting is enabled
                        if self.trading_mode.enable_shorting:
                            # Open short position
                            self.logger.info(f"Opening short position: {symbol}")
                            await self._setup_perps_settings(symbol)
                            quantity = await self._calculate_position_size(
                                symbol, current_price, trading_enums.TradeOrderSide.SELL
                            )
                        else:
                            self.logger.warning(f"No position to close and shorting is disabled for {symbol}")
                            return
                else:
                    # No existing position
                    if self.trading_mode.enable_shorting:
                        # Open short position
                        self.logger.info(f"Opening short position: {symbol}")
                        await self._setup_perps_settings(symbol)
                        quantity = await self._calculate_position_size(
                            symbol, current_price, trading_enums.TradeOrderSide.SELL
                        )
                    else:
                        self.logger.warning(f"No position to close and shorting is disabled for {symbol}")
                        return
            else:
                # Spot: Get available balance to sell
                base_currency = symbol.split("/")[0]
                portfolio = self.exchange_manager.exchange_personal_data.portfolio_manager.portfolio
                available = portfolio.get_currency_portfolio(base_currency).available
                
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
                price=current_price,
                is_perps=is_perps
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
            if ticker and "last" in ticker:
                return decimal.Decimal(str(ticker["last"]))
        except Exception as e:
            self.logger.error(f"Error getting price for {symbol}: {e}")
        
        return None
    
    async def _calculate_position_size(
        self, symbol: str, price: decimal.Decimal, side: trading_enums.TradeOrderSide
    ) -> decimal.Decimal:
        """
        Calculate position size based on portfolio percentage.
        For perps: accounts for leverage in position size calculation.
        
        Args:
            symbol: Trading symbol
            price: Current price
            side: Order side (BUY or SELL)
            
        Returns:
            Quantity to trade
        """
        try:
            portfolio = self.exchange_manager.exchange_personal_data.portfolio_manager.portfolio
            is_perps = self._is_perps_exchange()
            
            # Get available balance
            if side == trading_enums.TradeOrderSide.BUY:
                # For buy, use quote currency (e.g., USDT in BTC/USDT)
                quote_currency = symbol.split("/")[1]
                if is_perps:
                    # For perps, use total portfolio value (not just available)
                    # This is because perps use margin, not direct balance
                    portfolio_value = portfolio.get_currency_portfolio(quote_currency).total
                    available = portfolio_value
                else:
                    available = portfolio.get_currency_portfolio(quote_currency).available
            else:
                # For sell, use base currency (e.g., BTC in BTC/USDT)
                base_currency = symbol.split("/")[0]
                if is_perps:
                    # For perps sell (short), use quote currency for margin
                    quote_currency = symbol.split("/")[1]
                    portfolio_value = portfolio.get_currency_portfolio(quote_currency).total
                    available = portfolio_value
                else:
                    available = portfolio.get_currency_portfolio(base_currency).available
            
            # Calculate quantity based on position size percentage
            position_value = available * (self.trading_mode.position_size_percent / decimal.Decimal("100"))
            
            if side == trading_enums.TradeOrderSide.BUY:
                quantity = position_value / price
            else:
                quantity = position_value / price  # For perps short, also divide by price
            
            # For perps: apply leverage to position size (but margin requirement stays the same)
            if is_perps and self.trading_mode.leverage > 1:
                # Position size is multiplied by leverage, but margin is position_value
                quantity = quantity * self.trading_mode.leverage
            
            # Round to exchange precision
            quantity = self._round_to_exchange_precision(symbol, quantity)
            
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
        stop_loss_price: Optional[decimal.Decimal] = None,
        is_perps: bool = False
    ):
        """
        Create an order with optional stop-loss.
        For perps: uses trigger orders for stop-loss.
        
        Args:
            symbol: Trading symbol
            order_type: Type of order
            quantity: Order quantity
            price: Order price
            stop_loss_price: Stop-loss price
            is_perps: Whether this is a perps order
        """
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
        
        # Create stop-loss order if specified
        if stop_loss_price:
            if is_perps:
                # For perps, use trigger order for stop-loss
                # Hyperliquid supports trigger orders with triggerPrice
                # We'll use the standard stop-loss order type, but the exchange adapter
                # should handle the trigger order format
                sl_order_type = trading_enums.TraderOrderType.STOP_LOSS
                
                sl_order = trading_personal_data.create_order_instance(
                    trader=self.exchange_manager.trader,
                    order_type=sl_order_type,
                    symbol=symbol,
                    current_price=price,
                    quantity=quantity,
                    price=stop_loss_price
                )
                
                await self.exchange_manager.trader.create_order(sl_order)
            else:
                # Spot: standard stop-loss order
                sl_order_type = trading_enums.TraderOrderType.STOP_LOSS
                
                sl_order = trading_personal_data.create_order_instance(
                    trader=self.exchange_manager.trader,
                    order_type=sl_order_type,
                    symbol=symbol,
                    current_price=price,
                    quantity=quantity,
                    price=stop_loss_price
                )
                
                await self.exchange_manager.trader.create_order(sl_order)
    
    def _is_perps_exchange(self) -> bool:
        """Check if the exchange is configured for perps/futures trading."""
        if hasattr(self.exchange_manager, 'is_future'):
            return self.exchange_manager.is_future
        if hasattr(self.exchange_manager, 'exchange_type'):
            return self.exchange_manager.exchange_type == trading_enums.ExchangeTypes.FUTURE
        return False
    
    async def _setup_perps_settings(self, symbol: str):
        """
        Set up leverage and margin mode for perps trading.
        
        Args:
            symbol: Trading symbol
        """
        try:
            # Set leverage if configured
            if self.trading_mode.leverage > 1:
                if hasattr(self.trading_mode, 'set_leverage'):
                    await self.trading_mode.set_leverage(
                        symbol,
                        None,  # side=None for one-way mode
                        self.trading_mode.leverage
                    )
                elif hasattr(self.exchange_manager.exchange, 'set_leverage'):
                    await self.exchange_manager.exchange.set_leverage(
                        symbol,
                        self.trading_mode.leverage
                    )
                self.logger.info(f"Set leverage to {self.trading_mode.leverage}x for {symbol}")
            
            # Set margin mode if configured
            if self.trading_mode.margin_mode in ["cross", "isolated"]:
                is_isolated = self.trading_mode.margin_mode == "isolated"
                if hasattr(self.exchange_manager.exchange, 'set_symbol_margin_type'):
                    await self.exchange_manager.exchange.set_symbol_margin_type(
                        symbol,
                        is_isolated
                    )
                    self.logger.info(f"Set margin mode to {self.trading_mode.margin_mode} for {symbol}")
        except Exception as e:
            self.logger.warning(f"Could not set perps settings for {symbol}: {e}")
    
    async def _get_existing_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get existing position for a symbol (perps only).
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Position dict or None if no position
        """
        try:
            if not self._is_perps_exchange():
                return None
            
            positions = await self.exchange_manager.exchange.get_open_positions(symbol=symbol)
            if positions and len(positions) > 0:
                # Return first position (should only be one per symbol in one-way mode)
                return positions[0]
            return None
        except Exception as e:
            self.logger.debug(f"Error getting existing position for {symbol}: {e}")
            return None


# For compatibility with tentacles system
def get_trading_mode_class():
    """Return trading mode class for tentacles registration."""
    return ExternalSignalTradingMode

