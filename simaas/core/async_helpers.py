"""Async helper utilities for bridging sync/async contexts."""

import asyncio
import concurrent.futures


def run_coro_safely(coro):
    """Run a coroutine from sync context, handling nested event loop scenarios.

    If called from within an async context (event loop running), runs the
    coroutine in a thread pool to avoid nested event loop errors.
    """
    try:
        asyncio.get_running_loop()
        # In async context - run in thread pool
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    except RuntimeError:
        # No running loop - safe to use asyncio.run
        return asyncio.run(coro)
