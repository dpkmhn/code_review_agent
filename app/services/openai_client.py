from langsmith.wrappers import wrap_openai
from openai import AsyncOpenAI

from app.config import settings


def create_openai_client() -> AsyncOpenAI:
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    # Records each Responses call, with token usage, under the active LangGraph node run.
    return wrap_openai(client) if settings.langsmith_tracing else client
