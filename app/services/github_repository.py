from __future__ import annotations

import base64

import httpx

from app.config import settings


class GitHubRepositoryService:
    def __init__(self) -> None:
        self.base_url = "https://api.github.com"
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {settings.github_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def read_file(
        self,
        repository: str,
        path: str,
        ref: str,
    ) -> str:
        url = f"{self.base_url}/repos/{repository}/contents/{path}"

        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers=self.headers,
                params={"ref": ref},
            )

            response.raise_for_status()

        data = response.json()

        encoded_content = data["content"]

        return base64.b64decode(encoded_content).decode("utf-8")

    async def search_code(
        self,
        repository: str,
        query: str,
    ) -> list[dict]:
        url = f"{self.base_url}/search/code"

        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers=self.headers,
                params={
                    "q": f"{query} repo:{repository}",
                    "per_page": 10,
                },
            )

            response.raise_for_status()

        data = response.json()

        return [
            {
                "path": item["path"],
                "name": item["name"],
            }
            for item in data.get("items", [])
        ]


github_repository_service = GitHubRepositoryService()