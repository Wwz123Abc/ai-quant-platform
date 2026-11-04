"""Route for the question-and-answer assistant (read-only tools over your data; its cost counts in the AI budget)."""
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..ai import chat, engine, ledger
from ..config import get_settings
from .security import require_token

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)], tags=["ai"])


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=chat.MAX_QUESTION)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20, description="之前的几轮对话")


class ChatStep(BaseModel):
    tool: str = Field(description="调用的只读工具")
    args: dict[str, Any]
    result: str = Field(description="工具返回的开头部分，供核对")


class ChatAnswer(BaseModel):
    answer: str
    steps: list[ChatStep]
    llm_calls: int
    tokens_in: int
    tokens_out: int
    cost_cny: float


@router.post("/ai/chat", response_model=ChatAnswer, summary="向助手提问（只读，费用计入 AI 预算）")
def ai_chat(req: ChatRequest):
    engine.require_ready()
    s = get_settings()
    con = ledger.connect()
    try:
        ledger.check_budget(con, s=s, extra_runs=0)  # a question is cheap: only stop when the month is already used up
    finally:
        con.close()
    usage = engine.Usage()
    try:
        out = chat.ask(req.message, [m.model_dump() for m in req.history], usage=usage, s=s)
    finally:
        totals = usage.totals(s.ai_prices)  # tokens a failed question used were billed too
        if totals["llm_calls"]:
            con = ledger.connect()
            try:
                ledger.record_chat(con, totals, s=s)
            finally:
                con.close()
    return {**out, "cost_cny": out["cost_usd"] * s.ai_usd_cny}
