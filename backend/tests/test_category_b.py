"""The delegation boundary, enforced.

Every Category B function must raise ``NotImplementedError``. If one of these
tests starts failing, either the author implemented it (delete the case) or
something quietly filled it in, which is the thing this file exists to catch.
"""

from __future__ import annotations

import inspect
from typing import Any

import numpy as np
import pytest

from agent_app.core import agent, chunking, evaluate, retrieval, tools
from agent_app.core.retrieval import SearchFilters

EXPECTED_MESSAGE = "Category B — author writes this by hand"

CATEGORY_B: list[tuple[str, Any, tuple[Any, ...]]] = [
    ("chunking.chunk_posting", chunking.chunk_posting, (object(),)),
    ("chunking.chunk_profile_doc", chunking.chunk_profile_doc, ("text",)),
    ("retrieval.dense_scores", retrieval.dense_scores, (np.zeros(3), np.zeros((2, 3)))),
    ("retrieval.bm25_scores", retrieval.bm25_scores, ("q", [["a"], ["b"]])),
    ("retrieval.fuse", retrieval.fuse, ([np.zeros(2), np.zeros(2)],)),
    ("retrieval.search", retrieval.search, ("q", SearchFilters())),
    ("evaluate.recall_at_k", evaluate.recall_at_k, ([], [], 5)),
    ("evaluate.run_eval", evaluate.run_eval, ([],)),
]


@pytest.mark.parametrize(("name", "func", "args"), CATEGORY_B, ids=[c[0] for c in CATEGORY_B])
def test_category_b_function_is_not_implemented(
    name: str, func: Any, args: tuple[Any, ...]
) -> None:
    with pytest.raises(NotImplementedError) as caught:
        func(*args)
    assert str(caught.value) == EXPECTED_MESSAGE, name


def test_run_agent_is_not_implemented() -> None:
    # Separate from the table above because run_agent is declared as a
    # generator. It raises on call while the body is a bare `raise`; once the
    # author adds a `yield` it will raise on first iteration instead. Accept
    # either, so this test survives the transition.
    with pytest.raises(NotImplementedError) as caught:
        events = agent.run_agent("hello", [])
        if inspect.isgenerator(events):
            next(events)
    assert str(caught.value) == EXPECTED_MESSAGE


def test_every_tool_description_is_still_a_placeholder() -> None:
    descriptions = tools._all_descriptions(tools.TOOL_SCHEMAS)
    assert descriptions, "no descriptions found — the schemas are malformed"
    assert all(d == tools.TODO_DESCRIPTION for d in descriptions)
    assert tools.descriptions_written() is False


def test_the_category_b_list_has_not_shrunk() -> None:
    # plan.md's Category B table is exact. This pins the count so a function
    # cannot quietly leave the boundary.
    assert len(CATEGORY_B) + 1 == 9  # +1 for run_agent, tested separately
