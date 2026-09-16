"""Bounded async workers with cancellation cleanup and ordered results."""
import asyncio


async def bounded_map(function, items, concurrency):
    items = list(items)
    results = [None] * len(items)
    pending = iter(enumerate(items))

    async def worker():
        for index, item in pending:
            results[index] = await function(item)

    workers = [asyncio.create_task(worker())
               for _ in range(min(max(1, concurrency), len(items)))]
    try:
        await asyncio.gather(*workers)
    finally:
        for task in workers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
    return results
