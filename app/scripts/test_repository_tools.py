import asyncio

from app.tools.repository_tools import (
    find_references,
    read_file,
    search_code,
)


async def main() -> None:
    repository = "dpkmhn/financials"
    ref = "master"

    print("\n--- READ FILE ---")

    content = await read_file(
        repository=repository,
        path="README.md",
        ref=ref,
    )

    print(content[:500])

    print("\n--- SEARCH CODE ---")

    results = await search_code(
        repository=repository,
        query="invoice",
    )

    print(results)

    print("\n--- FIND REFERENCES ---")

    references = await find_references(
        repository=repository,
        symbol="invoice",
    )

    print(references)


if __name__ == "__main__":
    asyncio.run(main())