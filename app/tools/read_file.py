from app.services.github_repository import github_repository_service


async def read_file(
    repository: str,
    path: str,
    ref: str,
) -> str:
    return await github_repository_service.read_file(
        repository=repository,
        path=path,
        ref=ref,
    )