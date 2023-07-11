#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2021-2023 Valory AG
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

"""Tooling to perform subgraph requests from a behaviour."""

import json
from abc import ABC
from enum import Enum, auto
from typing import Any, Dict, Generator, Iterator, List, Optional, Tuple, cast

from packages.valory.skills.abstract_round_abci.behaviour_utils import BaseBehaviour
from packages.valory.skills.abstract_round_abci.models import ApiSpecs
from packages.valory.skills.market_manager_abci.graph_tooling.queries.omen import (
    questions,
)
from packages.valory.skills.market_manager_abci.models import (
    MarketManagerParams,
    SharedState,
)
from packages.valory.skills.market_manager_abci.rounds import SynchronizedData


def to_content(query: str) -> bytes:
    """Convert the given query string to payload content, i.e., add it under a `queries` key and convert it to bytes."""
    finalized_query = {"query": query}
    encoded_query = json.dumps(finalized_query, sort_keys=True).encode("utf-8")

    return encoded_query


def to_graphql_list(li: list) -> str:
    """Convert the given list to a string representing a list for a GraphQL query."""
    return repr(li).replace("'", '"')


class FetchStatus(Enum):
    """The status of a fetch operation."""

    SUCCESS = auto()
    IN_PROGRESS = auto()
    FAIL = auto()
    NONE = auto()


class QueryingBehaviour(BaseBehaviour, ABC):
    """Abstract behaviour that implements subgraph querying functionality."""

    def __init__(self, **kwargs: Any) -> None:
        """Initialize a querying behaviour."""
        super().__init__(**kwargs)
        self._call_failed: bool = False
        self._fetch_status: FetchStatus = FetchStatus.NONE
        self._creators_iterator: Iterator[
            Tuple[str, List[str]]
        ] = self.params.creators_iterator
        self._current_market: str = ""
        self._current_creators: List[str] = []

    @property
    def params(self) -> MarketManagerParams:
        """Get the params."""
        return cast(MarketManagerParams, self.context.params)

    @property
    def shared_state(self) -> SharedState:
        """Get the shared state."""
        return cast(SharedState, self.context.state)

    @property
    def synchronized_data(self) -> SynchronizedData:
        """Return the synchronized data."""
        return cast(SynchronizedData, super().synchronized_data)

    @property
    def synced_time(self) -> int:
        """Get the synchronized time among agents."""
        synced_time = self.shared_state.round_sequence.last_round_transition_timestamp
        return int(synced_time.timestamp())

    @property
    def current_subgraph(self) -> ApiSpecs:
        """Get a subgraph by prediction market's name."""
        return getattr(self.context, self._current_market)

    def _prepare_bets_fetching(self) -> bool:
        """Prepare for fetching a bet."""
        if self._fetch_status in (FetchStatus.SUCCESS, FetchStatus.NONE):
            res = next(self._creators_iterator, None)
            if res is None:
                return False
            self._current_market, self._current_creators = res

        if self._fetch_status == FetchStatus.FAIL:
            return False

        self._fetch_status = FetchStatus.IN_PROGRESS
        return True

    def _handle_response(
        self,
        res: Optional[Dict],
        res_context: str,
        sleep_on_fail: bool = True,
    ) -> Generator[None, None, Optional[Any]]:
        """Handle a response from a subgraph.

        :param res: the response to handle.
        :param res_context: the context of the current response.
        :param sleep_on_fail: whether we want to sleep if we fail to get the response's result.
        :return: the response's result, using the given keys. `None` if response is `None` (has failed).
        :yield: None
        """
        if res is None:
            self.context.logger.error(
                f"Could not get {res_context} from {self.current_subgraph.api_id}"
            )
            self._call_failed = True
            self.current_subgraph.increment_retries()

            if self.current_subgraph.is_retries_exceeded():
                self._fetch_status = FetchStatus.FAIL

            if sleep_on_fail:
                sleep_time = self.current_subgraph.retries_info.suggested_sleep_time
                yield from self.sleep(sleep_time)
            return None

        self.context.logger.info(f"Retrieved {res_context}: {res}.")
        self._call_failed = False
        self.current_subgraph.reset_retries()
        self._fetch_status = FetchStatus.SUCCESS
        return res

    def _fetch_bets(self) -> Generator[None, None, Optional[list]]:
        """Fetch questions from the current subgraph, for the current creators."""
        self._fetch_status = FetchStatus.IN_PROGRESS

        query = questions.substitute(
            creators=to_graphql_list(self._current_creators),
            slot_count=self.params.slot_count,
            opening_threshold=self.synced_time + self.params.opening_margin,
            languages=to_graphql_list(self.params.languages),
        )

        res_raw = yield from self.get_http_response(
            content=to_content(query),
            **self.current_subgraph.get_spec(),
        )
        res = self.current_subgraph.process_response(res_raw)

        bets = yield from self._handle_response(
            res,
            res_context="questions",
        )

        return bets