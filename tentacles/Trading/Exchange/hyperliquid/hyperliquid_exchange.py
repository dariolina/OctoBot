#  Drakkar-Software OctoBot-Tentacles
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
import typing

import octobot_trading.exchanges as exchanges
import octobot_trading.enums as trading_enums
import octobot_trading.exchanges.connectors.ccxt.constants as ccxt_constants


class HyperliquidConnector(exchanges.CCXTConnector):

    def __init__(self, *args, **kwargs):
        # Initialize main_account_wallet_address before parent init
        # (parent init calls _client_factory which needs this attribute)
        self.main_account_wallet_address = None
        
        # Extract config and exchange_manager from args/kwargs to get config before parent init
        config = None
        exchange_manager = None
        if len(args) > 0:
            config = args[0]
        if len(args) > 1:
            exchange_manager = args[1]
        if 'config' in kwargs:
            config = kwargs['config']
        if 'exchange_manager' in kwargs:
            exchange_manager = kwargs['exchange_manager']
        
        # Try to get main account wallet address from config dict directly
        if config and isinstance(config, dict):
            exchanges_config = config.get("exchanges", {})
            hyperliquid_config = exchanges_config.get("hyperliquid", {})
            if isinstance(hyperliquid_config, dict):
                options = hyperliquid_config.get("options", {})
                if isinstance(options, dict):
                    self.main_account_wallet_address = options.get("main_wallet_address", None)
        
        # Also try from exchange_manager.exchange_config if available
        if not self.main_account_wallet_address and exchange_manager and hasattr(exchange_manager, 'exchange_config'):
            exchange_config = exchange_manager.exchange_config
            # Try different ways to access options
            options = None
            if hasattr(exchange_config, 'get'):
                options = exchange_config.get("options", {})
            elif isinstance(exchange_config, dict):
                options = exchange_config.get("options", {})
            elif hasattr(exchange_config, 'options'):
                options = exchange_config.options
            
            if isinstance(options, dict):
                self.main_account_wallet_address = options.get("main_wallet_address", None)
        
        super().__init__(*args, **kwargs)
        
        # Also try after parent initialization in case config wasn't available before
        if not self.main_account_wallet_address:
            # Try from exchange_manager.exchange_config
            if hasattr(self, 'exchange_manager') and self.exchange_manager:
                exchange_config = getattr(self.exchange_manager, 'exchange_config', None)
                if exchange_config:
                    # Try different ways to access options
                    options = None
                    if hasattr(exchange_config, 'get'):
                        options = exchange_config.get("options", {})
                    elif isinstance(exchange_config, dict):
                        options = exchange_config.get("options", {})
                    elif hasattr(exchange_config, 'options'):
                        options = exchange_config.options
                    elif hasattr(exchange_config, '__dict__'):
                        # Try accessing as attribute
                        options = getattr(exchange_config, 'options', None)
                    
                    if isinstance(options, dict):
                        self.main_account_wallet_address = options.get("main_wallet_address", None)
                    
                    # Debug logging to see what we're getting
                    if hasattr(self, 'logger'):
                        self.logger.debug(f"exchange_config type: {type(exchange_config)}, "
                                        f"has 'get': {hasattr(exchange_config, 'get')}, "
                                        f"has 'options': {hasattr(exchange_config, 'options')}, "
                                        f"options: {options}")
            
            # Also try from config if available
            if not self.main_account_wallet_address and hasattr(self, 'config') and self.config:
                if isinstance(self.config, dict):
                    exchanges_config = self.config.get("exchanges", {})
                    hyperliquid_config = exchanges_config.get("hyperliquid", {})
                    if isinstance(hyperliquid_config, dict):
                        options = hyperliquid_config.get("options", {})
                        if isinstance(options, dict):
                            self.main_account_wallet_address = options.get("main_wallet_address", None)
        
        # Log the main account wallet address for debugging
        if self.main_account_wallet_address:
            if hasattr(self, 'logger'):
                self.logger.info(f"Hyperliquid main account wallet address configured: {self.main_account_wallet_address}")
            else:
                # Fallback logging if logger not available yet
                import logging
                logging.get_logger(self.__class__.__name__).info(
                    f"Hyperliquid main account wallet address configured: {self.main_account_wallet_address}"
                )
        else:
            if hasattr(self, 'logger'):
                self.logger.warning("Hyperliquid main account wallet address not found in config.options.main_wallet_address. "
                                  "Balance fetching may return empty results when using API/agent wallets.")
            else:
                # Fallback logging if logger not available yet
                import logging
                logging.get_logger(self.__class__.__name__).warning(
                    "Hyperliquid main account wallet address not found in config.options.main_wallet_address. "
                    "Balance fetching may return empty results when using API/agent wallets."
                )

    def _client_factory(
        self,
        force_unauth,
        keys_adapter: typing.Callable[[exchanges.ExchangeCredentialsData], exchanges.ExchangeCredentialsData]=None
    ) -> tuple:
        client, is_authenticated = super()._client_factory(force_unauth, keys_adapter=self._keys_adapter)
        # Patch fetch_balance to use correct type (spot or swap) and main account address
        # Hyperliquid fetchBalance defaults to type='swap', but we need to match exchange type
        # Also, API wallets (agent wallets) need to use main account address for info requests
        if client and hasattr(client, 'fetch_balance'):
            original_fetch_balance = client.fetch_balance
            main_account_address = self.main_account_wallet_address
            
            # Determine exchange type
            balance_type = "spot"  # default
            if hasattr(self, 'exchange_manager') and self.exchange_manager:
                if hasattr(self.exchange_manager, 'is_future') and self.exchange_manager.is_future:
                    balance_type = "swap"
                elif hasattr(self.exchange_manager, 'exchange_type'):
                    if self.exchange_manager.exchange_type == trading_enums.ExchangeTypes.FUTURE:
                        balance_type = "swap"
            
            import inspect
            
            if inspect.iscoroutinefunction(original_fetch_balance):
                # Async version
                async def patched_fetch_balance(params=None, **kwargs):
                    """
                    Wrapper to ensure correct type (spot/swap) and main account address are passed to Hyperliquid fetchBalance.
                    This fixes the issue where balance fetching returns empty dict {} because:
                    1. It defaults to type='swap' instead of matching exchange type
                    2. API wallets (agent wallets) need to use main account address for info requests
                    """
                    if params is None:
                        params = {}
                    if 'type' not in params:
                        params['type'] = balance_type
                    # Use main account wallet address if available (required for API/agent wallets)
                    if main_account_address and 'user' not in params:
                        params['user'] = main_account_address
                    return await original_fetch_balance(params=params, **kwargs)
            else:
                # Sync version
                def patched_fetch_balance(params=None, **kwargs):
                    """
                    Wrapper to ensure correct type (spot/swap) and main account address are passed to Hyperliquid fetchBalance.
                    This fixes the issue where balance fetching returns empty dict {} because:
                    1. It defaults to type='swap' instead of matching exchange type
                    2. API wallets (agent wallets) need to use main account address for info requests
                    """
                    if params is None:
                        params = {}
                    if 'type' not in params:
                        params['type'] = balance_type
                    # Use main account wallet address if available (required for API/agent wallets)
                    if main_account_address and 'user' not in params:
                        params['user'] = main_account_address
                    return original_fetch_balance(params=params, **kwargs)
            
            client.fetch_balance = patched_fetch_balance
        
        return client, is_authenticated

    def _keys_adapter(self, creds: exchanges.ExchangeCredentialsData) -> exchanges.ExchangeCredentialsData:
        # use api key and secret as wallet address and private key
        creds.wallet_address = creds.api_key
        creds.private_key = creds.secret
        creds.api_key = creds.secret = None
        return creds


class Hyperliquid(exchanges.RestExchange):
    DESCRIPTION = ""
    DEFAULT_CONNECTOR_CLASS = HyperliquidConnector

    FIX_MARKET_STATUS = True
    REQUIRE_ORDER_FEES_FROM_TRADES = True  # set True when get_order is not giving fees on closed orders and fees
    # should be fetched using recent trades.

    @classmethod
    def get_name(cls):
        return 'hyperliquid'

    def get_adapter_class(self):
        return HyperLiquidCCXTAdapter

    def get_additional_connector_config(self):
        # Determine exchange type from exchange manager
        exchange_type = None
        if hasattr(self, 'exchange_manager') and self.exchange_manager:
            if hasattr(self.exchange_manager, 'is_future') and self.exchange_manager.is_future:
                exchange_type = "swap"
            elif hasattr(self.exchange_manager, 'exchange_type'):
                if self.exchange_manager.exchange_type == trading_enums.ExchangeTypes.FUTURE:
                    exchange_type = "swap"
        
        # Default to spot if not determined
        if not exchange_type:
            exchange_type = "spot"
        
        # Support both spot and swap markets
        market_types = ["spot", "swap"] if exchange_type == "swap" else ["spot"]
        
        return {
            ccxt_constants.CCXT_OPTIONS: {
                "fetchMarkets": {
                    "types": market_types,
                },
                "defaultType": exchange_type,
                "fetchBalance": {
                    "type": exchange_type,
                }
            }
        }


class HyperLiquidCCXTAdapter(exchanges.CCXTAdapter):

    def fix_ticker(self, raw, **kwargs):
        fixed = super().fix_ticker(raw, **kwargs)
        fixed[trading_enums.ExchangeConstantsTickersColumns.TIMESTAMP.value] = \
            fixed.get(trading_enums.ExchangeConstantsTickersColumns.TIMESTAMP.value) or self.connector.client.seconds()
        return fixed

    def fix_market_status(self, raw, remove_price_limits=False, **kwargs):
        fixed = super().fix_market_status(raw, remove_price_limits=remove_price_limits, **kwargs)
        if not fixed:
            return fixed
        # hyperliquid min cost should be increased by 10% (a few cents above min cost is refused)
        limits = fixed[trading_enums.ExchangeConstantsMarketStatusColumns.LIMITS.value]
        limits[trading_enums.ExchangeConstantsMarketStatusColumns.LIMITS_COST.value][
            trading_enums.ExchangeConstantsMarketStatusColumns.LIMITS_COST_MIN.value
        ] = limits[trading_enums.ExchangeConstantsMarketStatusColumns.LIMITS_COST.value][
            trading_enums.ExchangeConstantsMarketStatusColumns.LIMITS_COST_MIN.value
        ] * 1.1

        return fixed
