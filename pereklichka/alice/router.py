from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from pereklichka.alice.dialog import Dialog
from pereklichka.alice.schemas import AliceRequest, AliceResponse, Reply
from pereklichka.db.repositories import CheckInRepository, LinkCodeRepository, WardRepository

router = APIRouter()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker.begin() as session:
        yield session


@router.post("/alice")
async def alice(
    body: AliceRequest, session: Annotated[AsyncSession, Depends(get_session)]
) -> AliceResponse:
    if body.request.original_utterance == "ping":
        return AliceResponse(response=Reply(text="ok", end_session=True))
    dialog = Dialog(
        WardRepository(session),
        LinkCodeRepository(session),
        CheckInRepository(session),
        now=datetime.now(UTC),
    )
    return await dialog.reply(body)
