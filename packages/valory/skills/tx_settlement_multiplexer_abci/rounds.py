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

"""This package contains the rounds of `TxSettlementMultiplexerAbciApp`."""

from enum import Enum
from typing import Any, Dict, Optional, Set, Tuple

from packages.valory.skills.abstract_round_abci.base import (
    AbciApp,
    AbciAppTransitionFunction,
    AppState,
    BaseSynchronizedData,
    CollectSameUntilThresholdRound,
    DegenerateRound,
    get_name,
)
from packages.valory.skills.decision_maker_abci.states.base import SynchronizedData
from packages.valory.skills.decision_maker_abci.states.bet_placement import (
    BetPlacementRound,
)
from packages.valory.skills.decision_maker_abci.states.decision_request import (
    DecisionRequestRound,
)


class Event(Enum):
    """Multiplexing events."""

    DECISION_REQUESTING_DONE = "decision_requesting_done"
    BET_PLACEMENT_DONE = "bet_placement_done"
    ROUND_TIMEOUT = "round_timeout"
    UNRECOGNIZED = "unrecognized"


class PostTxSettlementRound(CollectSameUntilThresholdRound):
    """A round that will be called after tx settlement is done."""

    payload_class: Any = object()
    synchronized_data_class = SynchronizedData

    def end_block(self) -> Optional[Tuple[BaseSynchronizedData, Enum]]:
        """
        The end block.

        This is a special type of round. No consensus is necessary here.
        There is no need to send a tx through, nor to check for a majority.
        We simply use this round to check which round submitted the tx,
        and move to the next state in accordance with that.
        """
        submitter_to_event: Dict[str, Event] = {
            DecisionRequestRound.auto_round_id(): Event.DECISION_REQUESTING_DONE,
            BetPlacementRound.auto_round_id(): Event.BET_PLACEMENT_DONE,
        }

        synced_data = SynchronizedData(self.synchronized_data.db)
        event = submitter_to_event.get(synced_data.tx_submitter, Event.UNRECOGNIZED)
        return synced_data, event


class FinishedDecisionRequestTxRound(DegenerateRound):
    """Finished decision requesting round."""


class FinishedBetPlacementTxRound(DegenerateRound):
    """Finished bet placement round."""


class FailedMultiplexerRound(DegenerateRound):
    """Round that represents failure in identifying the transmitter round."""


class TxSettlementMultiplexerAbciApp(AbciApp[Event]):
    """TxSettlementMultiplexerAbciApp

    Initial round: PostTxSettlementRound

    Initial states: {PostTxSettlementRound}

    Transition states:
        0. PostTxSettlementRound
            - decision requesting done: 1.
            - bet placement done: 2.
            - round timeout: 0.
            - unrecognized: 3.
        1. FinishedDecisionRequestTxRound
        2. FinishedBetPlacementTxRound
        3. FailedMultiplexerRound

    Final states: {FailedMultiplexerRound, FinishedBetPlacementTxRound, FinishedDecisionRequestTxRound}

    Timeouts:
        round timeout: 30.0
    """

    initial_round_cls: AppState = PostTxSettlementRound
    initial_states: Set[AppState] = {PostTxSettlementRound}
    transition_function: AbciAppTransitionFunction = {
        PostTxSettlementRound: {
            Event.DECISION_REQUESTING_DONE: FinishedDecisionRequestTxRound,
            Event.BET_PLACEMENT_DONE: FinishedBetPlacementTxRound,
            Event.ROUND_TIMEOUT: PostTxSettlementRound,
            Event.UNRECOGNIZED: FailedMultiplexerRound,
        },
        FinishedDecisionRequestTxRound: {},
        FinishedBetPlacementTxRound: {},
        FailedMultiplexerRound: {},
    }
    event_to_timeout: Dict[Event, float] = {
        Event.ROUND_TIMEOUT: 30.0,
    }
    final_states: Set[AppState] = {
        FinishedDecisionRequestTxRound,
        FinishedBetPlacementTxRound,
        FailedMultiplexerRound,
    }
    db_pre_conditions: Dict[AppState, Set[str]] = {
        PostTxSettlementRound: {get_name(SynchronizedData.tx_submitter)}
    }
    db_post_conditions: Dict[AppState, Set[str]] = {
        FinishedDecisionRequestTxRound: set(),
        FinishedBetPlacementTxRound: set(),
        FailedMultiplexerRound: set(),
    }