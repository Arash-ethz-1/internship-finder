"""Letter drafting and revision."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..core.letters import LetterError, ModelBusy, draft_letter, revise_letter
from .schemas import LetterResponse, LetterRevision

router = APIRouter(prefix="/api", tags=["letters"])


# Declared before the drafting route on purpose. A posting id contains slashes
# often enough that its parameter has to be `:path`, which is greedy -- with the
# other order, POST /api/letters/greenhouse:1/revise would match the drafting
# route with a posting id of "greenhouse:1/revise" and 404 confusingly.
@router.post("/letters/{posting_id:path}/revise", response_model=LetterResponse)
def post_letter_revision(posting_id: str, revision: LetterRevision) -> LetterResponse:
    """Apply one instruction to an existing draft.

    Distinct from re-drafting on purpose. "Make it shorter" is a change to
    *this* letter; regenerating rolls the dice again and throws away both the
    version the person liked and any hand edits they made to it. Which is also
    why the body carries the letter text: the editor's contents are the truth,
    not whatever happens to be on disk.

    Same error contract as drafting: 409 for something the person can fix,
    503 for a busy model.
    """
    try:
        letter = revise_letter(posting_id, revision.instruction, revision.letter)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ModelBusy as exc:
        raise HTTPException(503, str(exc), headers={"Retry-After": "20"}) from exc
    except LetterError as exc:
        raise HTTPException(409, str(exc)) from exc

    return LetterResponse(**letter.to_dict())


@router.post("/letters/{posting_id:path}", response_model=LetterResponse)
def post_letter(
    posting_id: str,
    chunks: int = Query(default=3, ge=1, le=10, description="profile extracts to ground in"),
) -> LetterResponse:
    """Draft a letter and return it with the profile chunks it was grounded in.

    409 means the letter could not be grounded — usually an empty ``profile/``
    — which is a state the person can fix, not a bug. 503 means the model was
    busy, which is a state nobody can fix except by waiting, and it used to
    arrive as a 500 with a traceback.
    """
    try:
        letter = draft_letter(posting_id, k=chunks)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ModelBusy as exc:
        raise HTTPException(503, str(exc), headers={"Retry-After": "20"}) from exc
    except LetterError as exc:
        raise HTTPException(409, str(exc)) from exc

    return LetterResponse(**letter.to_dict())
