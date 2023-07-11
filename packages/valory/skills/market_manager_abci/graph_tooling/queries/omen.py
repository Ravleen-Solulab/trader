# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2023 Valory AG
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#
# ------------------------------------------------------------------------------

"""Omen queries."""

from string import Template


questions = Template(
    """
    {
      fixedProductMarketMakers(
        where: {
          creator_in: ${creators},
          outcomeSlotCount: ${slot_count},
          openingTimestamp_gt: ${opening_threshold},
          language_in: ${languages},
          isPendingArbitration: false
        },
        orderBy: creationTimestamp
        orderDirection: desc
      ){
        id
        title
        creator
        fee
        openingTimestamp
        outcomeSlotCount
        outcomeTokenAmounts
        outcomeTokenMarginalPrices
        outcomes
        usdLiquidityMeasure
      }
    }
    """
)