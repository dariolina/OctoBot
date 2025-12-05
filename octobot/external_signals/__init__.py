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
External Signal Strategy Module

This module provides integration with external AI trading signals.
It's designed to work as a standalone trading strategy that fetches
signals from a REST API and executes trades based on those signals.
"""

from octobot.external_signals import strategy_evaluator
from octobot.external_signals import trading_mode

__all__ = [
    "strategy_evaluator",
    "trading_mode",
]

