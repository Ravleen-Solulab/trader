# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2023-2024 Valory AG
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

"""This module contains the rounds for the Staking ABCI application."""

from abc import ABC
from enum import Enum
from typing import Dict, Optional, Set, Tuple, Type, cast

from packages.valory.contracts.service_staking_token.contract import StakingState
from packages.valory.skills.abstract_round_abci.base import (
    AbciApp,
    AbciAppTransitionFunction,
    AbstractRound,
    AppState,
    BaseSynchronizedData,
    CollectSameUntilThresholdRound,
    CollectionRound,
    DegenerateRound,
    DeserializedCollection,
    get_name,
)
from packages.valory.skills.staking_abci.payloads import CallCheckpointPayload
from packages.valory.skills.transaction_settlement_abci.rounds import (
    SynchronizedData as TxSettlementSyncedData,
)


class Event(Enum):
    """Event enumeration for the staking skill."""

    DONE = "done"
    ROUND_TIMEOUT = "round_timeout"
    NO_MAJORITY = "no_majority"
    SERVICE_NOT_STAKED = "service_not_staked"
    SERVICE_EVICTED = "service_evicted"
    NEXT_CHECKPOINT_NOT_REACHED_YET = "next_checkpoint_not_reached_yet"


class SynchronizedData(TxSettlementSyncedData):
    """Class to represent the synchronized data.

    This data is replicated by the tendermint application.
    """

    def _get_deserialized(self, key: str) -> DeserializedCollection:
        """Strictly get a collection and return it deserialized."""
        serialized = self.db.get_strict(key)
        return CollectionRound.deserialize_collection(serialized)

    @property
    def tx_submitter(self) -> str:
        """Get the round that submitted a tx to transaction_settlement_abci."""
        return str(self.db.get_strict("tx_submitter"))

    @property
    def service_staking_state(self) -> StakingState:
        """Get the service's staking state."""
        return StakingState(self.db.get("service_staking_state", 0))

    @property
    def participant_to_checkpoint(self) -> DeserializedCollection:
        """Get the participants to the checkpoint round."""
        return self._get_deserialized("participant_to_checkpoint")


class CallCheckpointRound(CollectSameUntilThresholdRound):
    """A round for the checkpoint call preparation."""

    payload_class = CallCheckpointPayload
    done_event: Enum = Event.DONE
    no_majority_event: Enum = Event.NO_MAJORITY
    selection_key = (
        get_name(SynchronizedData.tx_submitter),
        get_name(SynchronizedData.most_voted_tx_hash),
        get_name(SynchronizedData.service_staking_state),
    )
    collection_key = get_name(SynchronizedData.participant_to_checkpoint)
    synchronized_data_class = SynchronizedData

    def end_block(self) -> Optional[Tuple[BaseSynchronizedData, Enum]]:
        """Process the end of the block."""
        res = super().end_block()
        if res is None:
            return None

        synced_data, event = cast(Tuple[SynchronizedData, Enum], res)

        if event != Event.DONE:
            return res

        if synced_data.service_staking_state == StakingState.UNSTAKED:
            return synced_data, Event.SERVICE_NOT_STAKED

        if synced_data.service_staking_state == StakingState.EVICTED:
            return synced_data, Event.SERVICE_EVICTED

        if synced_data.most_voted_tx_hash is None:
            return synced_data, Event.NEXT_CHECKPOINT_NOT_REACHED_YET

        return res


class CheckpointCallPreparedRound(DegenerateRound, ABC):
    """A round that represents staking has finished with a checkpoint call safe tx prepared."""


class FinishedStakingRound(DegenerateRound, ABC):
    """A round that represents staking has finished."""


class ServiceEvictedRound(DegenerateRound, ABC):
    """A round that terminates the service if it has been evicted."""

    def end_block(self) -> Optional[Tuple[BaseSynchronizedData, Enum]]:
        """End block."""


class StakingAbciApp(AbciApp[Event]):  # pylint: disable=too-few-public-methods
    """StakingAbciApp

    Initial round: CallCheckpointRound

    Initial states: {CallCheckpointRound}

    Transition states:
        0. CallCheckpointRound
            - done: 1.
            - service not staked: 2.
            - service evicted: 3.
            - next checkpoint not reached yet: 2.
            - round timeout: 0.
            - no majority: 0.
        1. CheckpointCallPreparedRound
        2. FinishedStakingRound
        3. ServiceEvictedRound

    Final states: {CheckpointCallPreparedRound, FinishedStakingRound, ServiceEvictedRound}

    Timeouts:
        round timeout: 30.0
    """

    initial_round_cls: Type[AbstractRound] = CallCheckpointRound
    transition_function: AbciAppTransitionFunction = {
        CallCheckpointRound: {
            Event.DONE: CheckpointCallPreparedRound,
            Event.SERVICE_NOT_STAKED: FinishedStakingRound,
            Event.SERVICE_EVICTED: ServiceEvictedRound,
            Event.NEXT_CHECKPOINT_NOT_REACHED_YET: FinishedStakingRound,
            Event.ROUND_TIMEOUT: CallCheckpointRound,
            Event.NO_MAJORITY: CallCheckpointRound,
        },
        CheckpointCallPreparedRound: {},
        FinishedStakingRound: {},
        ServiceEvictedRound: {},
    }
    cross_period_persisted_keys = frozenset(
        {get_name(SynchronizedData.service_staking_state)}
    )
    final_states: Set[AppState] = {
        CheckpointCallPreparedRound,
        FinishedStakingRound,
        ServiceEvictedRound,
    }
    event_to_timeout: Dict[Event, float] = {
        Event.ROUND_TIMEOUT: 30.0,
    }
    db_pre_conditions: Dict[AppState, Set[str]] = {CallCheckpointRound: set()}
    db_post_conditions: Dict[AppState, Set[str]] = {
        CheckpointCallPreparedRound: {
            get_name(SynchronizedData.tx_submitter),
            get_name(SynchronizedData.most_voted_tx_hash),
            get_name(SynchronizedData.service_staking_state),
        },
        FinishedStakingRound: {
            get_name(SynchronizedData.service_staking_state),
        },
        ServiceEvictedRound: {
            get_name(SynchronizedData.service_staking_state),
        },
    }
