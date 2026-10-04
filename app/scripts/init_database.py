"""Initialize application tables and LangGraph checkpoint migrations."""

import asyncio

from app.config import settings
from app.persistence.database import database_pool, initialize_database, postgres_loop_factory


async def main():
    if settings.database_url is None:
        raise SystemExit("Set database_url before initializing PostgreSQL.")
    async with database_pool(settings.database_url.get_secret_value()) as pool:
        await initialize_database(pool)
    print("Review and checkpoint tables initialized.")


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=postgres_loop_factory)
