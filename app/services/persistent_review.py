"""Run or recover a review from its latest durable graph checkpoint."""

import asyncio


class PersistentReviewRunner:
    def __init__(self, store, graph):
        self.store = store
        self.graph = graph

    async def run(self, review_id):
        async with self.store.lock(review_id):
            review = await self.store.get(review_id)
            if review.status == "reviewed":
                return review
            config = {"configurable": {"thread_id": str(review_id)}}
            await self.store.status(review_id, "running")
            try:
                snapshot = await self.graph.aget_state(config)
                if snapshot.values:
                    # A process may have died after the final checkpoint but
                    # before committing the final application projection.
                    if not snapshot.next and "review" in snapshot.values:
                        await self.store.project(review_id, snapshot.values, complete=True)
                        return await self.store.get(review_id)
                    graph_input = None
                else:
                    graph_input = {"pull_request": review.pull_request}
                async for state in self.graph.astream(
                    graph_input, config=config, stream_mode="values", durability="sync",
                ):
                    await self.store.project(review_id, state)
                snapshot = await self.graph.aget_state(config)
                if snapshot.next or "review" not in snapshot.values:
                    raise RuntimeError("Review graph did not finish")
                await self.store.project(review_id, snapshot.values, complete=True)
            except (Exception, asyncio.CancelledError):
                # Do not persist exception strings: providers may include secrets.
                await self.store.status(review_id, "failed", "Review interrupted; resume from the saved checkpoint.")
                raise
            return await self.store.get(review_id)
