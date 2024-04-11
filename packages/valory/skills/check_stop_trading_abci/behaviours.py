# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2024 Valory AG
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

"""This module contains the behaviours for the check stop trading skill."""

import math
from typing import Any, Generator, Set, Type, cast

from packages.valory.contracts.mech.contract import Mech as MechContract
from packages.valory.skills.abstract_round_abci.base import get_name
from packages.valory.skills.abstract_round_abci.behaviour_utils import BaseBehaviour
from packages.valory.skills.abstract_round_abci.behaviours import AbstractRoundBehaviour
from packages.valory.skills.check_stop_trading_abci.models import CheckStopTradingParams
from packages.valory.skills.check_stop_trading_abci.payloads import CheckStopTradingPayload
from packages.valory.skills.check_stop_trading_abci.rounds import (
    CheckStopTradingRound,
    CheckStopTradingAbciApp,
)
from packages.valory.skills.staking_abci.behaviours import (
    StakingInteractBaseBehaviour,
    WaitableConditionType,
)
from packages.valory.contracts.service_staking_token.contract import StakingState


# Liveness ratio from the staking contract is expressed in calls per 10**18 seconds.
LIVENESS_RATIO_SCALE_FACTOR = 10**18

# A safety margin in case there is a delay between the moment the KPI condition is
# satisfied, and the moment where the checkpoint is called.
REQUIRED_MECH_REQUESTS_SAFETY_MARGIN = 1

class CheckStopTradingBehaviour(StakingInteractBaseBehaviour):
    """A behaviour that checks stop trading conditions."""

    matching_round = CheckStopTradingRound

    @property
    def mech_request_count(self) -> int:
        """Get the liveness period."""
        return self._mech_request_count

    @mech_request_count.setter
    def mech_request_count(self, mech_request_count: int) -> None:
        """Set the liveness period."""
        self._mech_request_count = mech_request_count

    def _get_mech_request_count(self) -> WaitableConditionType:
        """Get the mech request count."""
        status = yield from self.contract_interact(
            contract_address=self.params.mech_contract_address,
            contract_public_id=MechContract.contract_id,
            contract_callable="get_requests_count",
            data_key="requests_count",
            placeholder=get_name(CheckStopTradingBehaviour.mech_request_count),
            address=self.synchronized_data.safe_contract_address,
        )
        return status

    @property
    def is_first_period(self) -> bool:
        """Return whether it is the first period of the service."""
        return self.synchronized_data.period_count == 0

    @property
    def params(self) -> CheckStopTradingParams:
        """Return the params."""
        return cast(CheckStopTradingParams, self.context.params)

    def is_staking_kpi_met(self) -> WaitableConditionType:
        """Return whether the staking KPI has been met (only for staked services)."""
        yield from self.wait_for_condition_with_sleep(self._check_service_staked)
        self.context.logger.info(f"{self.service_staking_state=}")
        if self.service_staking_state != StakingState.STAKED:
            return False

        yield from self.wait_for_condition_with_sleep(self._get_mech_request_count)
        mech_request_count = self.mech_request_count
        self.context.logger.info(f"{self.mech_request_count=}")

        yield from self.wait_for_condition_with_sleep(self._get_service_info)
        mech_request_count_on_last_checkpoint = self.service_info[2][1]
        self.context.logger.info(f"{mech_request_count_on_last_checkpoint=}")

        yield from self.wait_for_condition_with_sleep(self._get_ts_checkpoint)
        last_ts_checkpoint = self.ts_checkpoint
        self.context.logger.info(f"{last_ts_checkpoint=}")

        yield from self.wait_for_condition_with_sleep(self._get_liveness_period)
        liveness_period = self.liveness_period
        self.context.logger.info(f"{liveness_period=}")

        yield from self.wait_for_condition_with_sleep(self._get_liveness_ratio)
        liveness_ratio = self.liveness_ratio
        self.context.logger.info(f"{liveness_ratio=}")

        mech_requests_since_last_cp = mech_request_count - mech_request_count_on_last_checkpoint
        self.context.logger.info(f"{mech_requests_since_last_cp=}")

        current_timestamp = self.synced_timestamp
        self.context.logger.info(f"{current_timestamp=}")

        required_mech_requests = math.ceil(max(
            (current_timestamp - last_ts_checkpoint) * liveness_ratio / LIVENESS_RATIO_SCALE_FACTOR,
            (liveness_period) * liveness_ratio / LIVENESS_RATIO_SCALE_FACTOR
        )) + REQUIRED_MECH_REQUESTS_SAFETY_MARGIN
        self.context.logger.info(f"{required_mech_requests=}")

        if mech_requests_since_last_cp >= required_mech_requests:
            return True
        return False

    def async_act(self) -> Generator:
        """Do the action."""
        with self.context.benchmark_tool.measure(self.behaviour_id).local():

            # This is a "hacky" way of getting required data initialized on
            # the Trader: On first period, the FSM needs to initialize some
            # data on the trading branch so that it is available in the
            # cross-period persistent keys.
            if self.is_first_period:
                stop_trading = False
            else:
                stop_trading_conditions = []

                disable_trading = self.params.disable_trading
                self.context.logger.info(f"{disable_trading=}")
                stop_trading_conditions.append(disable_trading)

                if self.params.stop_trading_if_staking_kpi_met:
                    staking_kpi_met = yield from self.is_staking_kpi_met()
                    self.context.logger.info(f"{staking_kpi_met=}")
                    stop_trading_conditions.append(staking_kpi_met)

                stop_trading = any(stop_trading_conditions)

            self.context.logger.info(f"{stop_trading=}")
            payload = CheckStopTradingPayload(
                self.context.agent_address, stop_trading
            )

        with self.context.benchmark_tool.measure(self.behaviour_id).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()
            self.set_done()


class CheckStopTradingRoundBehaviour(AbstractRoundBehaviour):
    """This behaviour manages the consensus stages for the check stop trading behaviour."""

    initial_behaviour_cls = CheckStopTradingBehaviour
    abci_app_cls = CheckStopTradingAbciApp
    behaviours: Set[Type[BaseBehaviour]] = {CheckStopTradingBehaviour}  # type: ignore
