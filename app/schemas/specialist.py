from typing import Literal

from pydantic import BaseModel, Field


Specialist = Literal["correctness", "security", "performance"]
# Fixed order keeps fan-out, budgets and merged findings deterministic.
SPECIALISTS: tuple[Specialist, ...] = ("correctness", "security", "performance")


class SpecialistSelection(BaseModel):
    specialists: list[Specialist] = Field(
        description="Specialist reviewers whose area this change plausibly affects.",
    )
    reason: str
