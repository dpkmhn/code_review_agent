import hashlib
import json
from difflib import unified_diff
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.github import PullRequestData, PullRequestFile


class ExpectedBug(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    file: str
    start_line: int = Field(gt=0)
    end_line: int = Field(gt=0)
    trigger: str = Field(min_length=1)
    consequence: str = Field(min_length=1)


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    base: dict[str, str]
    head: dict[str, str]
    expected_bugs: list[ExpectedBug]

    @model_validator(mode="after")
    def validate_labels(self):
        if self.base == self.head:
            raise ValueError("A PR fixture must change the repository")
        if len({bug.id for bug in self.expected_bugs}) != len(self.expected_bugs):
            raise ValueError("Bug IDs must be unique per case")
        for bug in self.expected_bugs:
            if self.base.get(bug.file) == self.head.get(bug.file):
                raise ValueError("Bug must be in a changed file")
            if not bug.start_line <= bug.end_line <= len(self.head.get(bug.file, "").splitlines()):
                raise ValueError("Bug location must exist in the head snapshot")
        return self

    def pull_request(self, number: int) -> PullRequestData:
        files = []
        for path in sorted(self.base.keys() | self.head.keys()):
            before, after = self.base.get(path, ""), self.head.get(path, "")
            if before == after:
                continue
            diff = list(unified_diff(before.splitlines(), after.splitlines(), lineterm=""))
            patch = "\n".join(diff[2:])
            additions = sum(line.startswith("+") for line in diff[2:])
            deletions = sum(line.startswith("-") for line in diff[2:])
            files.append(PullRequestFile(
                filename=path, status="added" if path not in self.base else
                "removed" if path not in self.head else "modified",
                additions=additions, deletions=deletions, changes=additions + deletions, patch=patch,
            ))
        return PullRequestData(
            owner="evaluation", repository=self.id, number=number, title=self.title, body=None,
            base_branch="main", head_branch="candidate", base_sha=snapshot_hash(self.base),
            head_sha=snapshot_hash(self.head), files=files,
        )


def snapshot_hash(snapshot: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def load_dataset(path: Path) -> tuple[list[EvaluationCase], str]:
    raw = path.read_bytes()
    cases = [EvaluationCase.model_validate(case) for case in json.loads(raw)]
    if not cases or len({case.id for case in cases}) != len(cases):
        raise ValueError("Dataset must be nonempty with unique case IDs")
    return cases, hashlib.sha256(raw).hexdigest()


class FixtureRepository:
    """Search the base snapshot, then read the exact requested revision."""

    def __init__(self, case: EvaluationCase):
        self.case = case
        self.snapshots = {snapshot_hash(case.base): case.base, snapshot_hash(case.head): case.head}

    def _check_scope(self, owner, repository):
        if (owner, repository) != ("evaluation", self.case.id):
            raise ValueError("Repository is outside this evaluation case")

    async def read_file(self, *, owner, repository, path, ref):
        self._check_scope(owner, repository)
        return self.snapshots[ref][path]

    async def list_tree(self, *, owner, repository, ref):
        self._check_scope(owner, repository)
        return {"paths": sorted(self.snapshots[ref]), "truncated": False}

    async def search_code(self, *, owner, repository, query):
        self._check_scope(owner, repository)
        # Deliberately simpler than GitHub query syntax. Literal, case-insensitive search.
        return [{"path": path} for path, content in sorted(self.case.base.items())
                if query.casefold() in content.casefold() or query.casefold() in path.casefold()][:10]

    async def find_references(self, *, owner, repository, symbol):
        return await self.search_code(owner=owner, repository=repository, query=symbol)
