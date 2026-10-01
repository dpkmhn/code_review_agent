import json
from openai import AsyncOpenAI
from app.config import settings
from app.schemas.ai_review import ReviewResult
from app.schemas.github import PullRequestData

# Explicit instructions forcing the LLM to focus on logic/bugs rather than styling or formatting
SYSTEM_PROMPT = """
You are a senior software engineer reviewing a GitHub pull request.

Your job is to identify genuine correctness bugs introduced by the pull request.

Focus on:
- incorrect behaviour
- runtime errors
- broken edge cases
- incorrect conditions
- incorrect data handling
- security issues
- regressions

Do not report:
- formatting issues
- subjective style preferences
- naming preferences
- missing comments
- harmless refactoring choices

Only report an issue when there is reasonable evidence in the supplied diff.

If there are no genuine issues, return an empty findings list.

Do not invent repository context that is not present in the diff.
"""

class LLMReviewService:

    def __init__(self) -> None:
        # Initialize OpenAI async worker with configured app credentials
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def review_pull_request(self, pull_request: PullRequestData) -> ReviewResult:
        # Flatten all separate Pydantic file object chunks into a unified text diff block
        diff = self._build_diff(pull_request)

        # Trigger structured LLM generation forcing JSON conforming precisely to our Pydantic schema
        response = await self.client.responses.create(
            model="gpt-6-luna",
            instructions=SYSTEM_PROMPT,
            input=f"Review the following pull request.\n\nTitle:\n{pull_request.title}\n\nDescription:\n{pull_request.body or 'No description provided.'}\n\nDiff:\n{diff}",
            text={
                "format": {
                    "type": "json_schema",
                    "name": "code_review",
                    "strict": True,
                    "schema": ReviewResult.model_json_schema(), # Inject target pydantic validation blueprint
                }
            },
        )

        # Parse and return validated Pydantic object directly from the guaranteed response string
        return ReviewResult.model_validate_json(response.output_text)

    # @staticmethod- belongs to the class namespace but does not access any instance or class data.
    @staticmethod
    def _build_diff(pull_request: PullRequestData) -> str:
        # Assemble separate file patches down into a single coherent review text stream
        sections = []
        for file in pull_request.files:
            if not file.patch: # Skip binary, untracked, or empty file patches
                continue
            sections.append(f"\nFILE: {file.filename}\n\n{file.patch}\n")
        return "\n".join(sections)
