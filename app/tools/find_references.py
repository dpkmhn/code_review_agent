from app.tools.search_code import search_code


async def find_references(
    repository: str,
    symbol: str,
) -> list[dict]:
    return await search_code(
        repository=repository,
        query=symbol,
    )