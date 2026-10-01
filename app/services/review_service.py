from app.schemas.github import PullRequestData
from app.github_client import GitHubClient

class ReviewService:

    def __init__(self):
        # Initialize internal GitHub API client dependency
        self.github_client = GitHubClient()

    async def ingest_pull_request(self, repository: str, pull_request_number: int) -> PullRequestData:
        # Parse text into parts and fetch pull request details asynchronously
        owner, repo = self._parse_repository(repository)
        return await self.github_client.get_pull_request(
            owner=owner, repository=repo, pull_number=pull_request_number
        )

    def _parse_repository(self, repository: str) -> tuple[str, str]:
        # Split string by forward slash boundaries to isolate account and project names
        parts = repository.split("/")
        if len(parts) != 2:
            raise ValueError("Repository must have format 'owner/repository'")
        return parts[0], parts[1]
