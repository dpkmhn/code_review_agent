import httpx, base64
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

    # Read a file from a specific branch or commit
    async def download_archive(self, owner: str, repository: str, ref: str) -> bytes:
        """Download a bounded snapshot at the revision injected by the graph."""
        url = f"{self.BASE_URL}/repos/{owner}/{repository}/zipball/{ref}"
        content = bytearray()
        async with httpx.AsyncClient(headers=self._headers(), timeout=30.0,
                                     follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 25 * 1024 * 1024:
                        raise ValueError("Repository archive exceeds 25 MiB.")
        return bytes(content)

    async def read_file(
        self,
        owner: str,
        repository: str,
        path: str,
        ref: str,
    ) -> str:
        url = f"{self.BASE_URL}/repos/{owner}/{repository}/contents/{path}"

        async with httpx.AsyncClient(
            headers=self._headers(),
            timeout=30.0,
        ) as client:
            res = await client.get(
                url,
                params={"ref": ref},
            )
            res.raise_for_status()

        data = res.json()

        # GitHub returns file content as Base64
        return base64.b64decode(
            data["content"]
        ).decode("utf-8")


    # Search the repository for code or a symbol
    async def search_code(
        self,
        owner: str,
        repository: str,
        query: str,
    ) -> list[dict]:
        url = f"{self.BASE_URL}/search/code"

        async with httpx.AsyncClient(
            headers=self._headers(),
            timeout=30.0,
        ) as client:
            res = await client.get(
                url,
                params={
                    "q": f"{query} repo:{owner}/{repository}",
                    "per_page": 10,
                },
            )
            res.raise_for_status()

        data = res.json()

        return [
            {
                "name": item["name"],
                "path": item["path"],
            }
            for item in data.get("items", [])
        ]


    # Find usages/references of a symbol
    async def find_references(
        self,
        owner: str,
        repository: str,
        symbol: str,
    ) -> list[dict]:
        return await self.search_code(
            owner=owner,
            repository=repository,
            query=symbol,
        )
