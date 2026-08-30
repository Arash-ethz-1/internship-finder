"""Letter drafting."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..core.letters import LetterError, draft_letter
from .schemas import LetterResponse

router = APIRouter(prefix="/api", tags=["letters"])


@router.post("/letters/{posting_id:path}", response_model=LetterResponse)
def post_letter(
    posting_id: str,
    chunks: int = Query(default=3, ge=1, le=10, description="profile extracts to ground in"),
) -> LetterResponse:
    """Draft a letter and return it with the profile chunks it was grounded in.

    Returns 501 until ``retrieval.search`` is written, naming the function so
    the message is actionable rather than a bare failure.
    """
    try:
        letter = draft_letter(posting_id, k=chunks)
    except NotImplementedError as exc:
        raise HTTPException(
            501,
            "Letter drafting needs agent_app.core.retrieval.search, which is "
            "Category B and not implemented yet.",
        ) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except LetterError as exc:
        raise HTTPException(409, str(exc)) from exc

    return LetterResponse(**letter.to_dict())
