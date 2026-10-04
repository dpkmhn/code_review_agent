from app.services.github_repository import github_repository_service


async def search_code(
    repository: str,
    query: str,
) -> list[dict]:
    return await github_repository_service.search_code(
        repository=repository,
        query=query,
    )