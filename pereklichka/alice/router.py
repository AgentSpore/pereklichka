from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from pereklichka.alice.dialog import Dialog
from pereklichka.alice.schemas import AliceRequest, AliceResponse, Reply

router = APIRouter()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


@router.post("/alice")
async def alice(
    body: AliceRequest, request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> AliceResponse:
    if body.session.skill_id != request.app.state.settings.skill_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    if body.request.original_utterance == "ping":
        return AliceResponse(response=Reply(text="ok", end_session=True))
    reply = await Dialog(session, now=datetime.now(UTC), settings=request.app.state.settings).reply(
        body
    )
    # INVARIANT: commit before answering — a yield-dependency commits after the response is sent,
    # so a lost write would still be announced to the speaker as saved.
    await session.commit()
    return reply
