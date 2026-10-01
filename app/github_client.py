import httpx
from app.config import settings
from app.schemas.github import PullRequestData, PullRequestFile

class GitHubClient:
    BASE_URL = "https://api.github.com"
    
    # Build authenticated GitHub API headers
    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"
        return headers

    # Fetch, merge, and structure full PR details and files
    async def get_pull_request(self, owner: str, repository: str, pull_number: int) -> PullRequestData:
        url = f"{self.BASE_URL}/repos/{owner}/{repository}/pulls/{pull_number}"
        
        async with httpx.AsyncClient(headers=self._headers(), timeout=30.0) as client:
            res = await client.get(url)
            res.raise_for_status()
            pr = res.json()
            
            # Fetch files asynchronously using the active client session
            files = await self._get_pull_request_files(client, owner, repository, pull_number)

        return PullRequestData(
            owner=owner, repository=repository, number=pr["number"], title=pr["title"], body=pr["body"],
            base_branch=pr["base"]["ref"], head_branch=pr["head"]["ref"],
            base_sha=pr["base"]["sha"], head_sha=pr["head"]["sha"], files=files
        )

    # Paginate through GitHub API to collect every changed file
    async def _get_pull_request_files(self, client: httpx.AsyncClient, owner: str, repository: str, pull_number: int) -> list[PullRequestFile]:
        url = f"{self.BASE_URL}/repos/{owner}/{repository}/pulls/{pull_number}/files"
        files, page = [], 1

        while True:
            res = await client.get(url, params={"per_page": 100, "page": page})
            res.raise_for_status()
            data = res.json()

            # Parse page payloads directly into Pydantic models
            files.extend(PullRequestFile(
                filename=f["filename"], status=f["status"], additions=f["additions"],
                deletions=f["deletions"], changes=f["changes"], patch=f.get("patch")
            ) for f in data)

            if len(data) < 100: break # Break loop if we hit the last page
            page += 1

        return files
