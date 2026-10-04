from pydantic import BaseModel, Field


class ContextRequest(BaseModel):
    needs_context: bool = Field(
        description="Whether repository context is required before reviewing the PR."
    )

    reason: str = Field(
        description="Why additional repository context is or is not needed."
    )

    queries: list[str] = Field(
        default_factory=list,
        description="Code symbols or terms that should be searched for."
    )