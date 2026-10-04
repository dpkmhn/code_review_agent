from openai import AsyncOpenAI

from app.config import settings
from app.schemas.context import ContextRequest
from app.schemas.github import PullRequestData
from app.services.llm_review_service import LLMReviewService, bounded_description


class ContextService:

    def __init__(self, usage_observer=None) -> None:
        self.usage_observer = usage_observer
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def analyse_context_need(
        self,
        pull_request: PullRequestData,
    ) -> ContextRequest:

        changed_files = LLMReviewService._build_diff(pull_request)

        prompt = f"""
You are analysing a GitHub pull request before performing a code review.

Determine whether understanding code outside the supplied diff would materially
help identify bugs.

Request context when the changed behaviour depends on things such as:

- callers
- referenced functions
- shared models
- interfaces
- configuration
- behaviour implemented elsewhere

Do NOT request context for trivial documentation, formatting, comment,
or obviously self-contained changes.

Pull request:

Title:
{pull_request.title}

Description:
{bounded_description(pull_request.body)}

Changes:
{changed_files}
"""

        response = await self.client.responses.parse(
            model="gpt-6-luna",
            input=prompt,
            text_format=ContextRequest,
        )

        if self.usage_observer is not None:
            self.usage_observer(response)
        context_request = response.output_parsed

        if context_request is None:
            raise ValueError(
                "OpenAI did not return a structured context request."
            )

        return context_request
