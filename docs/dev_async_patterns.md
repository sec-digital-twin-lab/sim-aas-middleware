# Async Patterns

This document explains the async/sync architecture used in the middleware, why it was designed
this way, and how to avoid common pitfalls when extending the codebase.

## Architecture Overview

The middleware uses a **hybrid async design**: the service layer is async-native, but long-running
servers (P2P, REST) run in their own daemon threads with isolated event loops.

```
CLI (sync entry point)
  --> asyncio.run(main())          <-- ONE asyncio.run per CLI command
    --> Service method (async)
      --> await P2P call

REST (FastAPI async, daemon thread)
  --> Handler (async)
    --> Service method (async)
      --> await P2P call           <-- Shared event loop within thread

Processor (worker thread)
  --> SyncNamespace wrapper        <-- Thread-local event loop
    --> Calls async service methods from sync context
```

## Why Daemon Threads with Isolated Event Loops?

P2P and REST services run in daemon threads (not on the main event loop) for three reasons:

1. **Long-running servers**: They continuously accept connections, independent of application
   logic. Sharing the main event loop would block application code during I/O waits.

2. **Thread isolation**: Each daemon thread has its own event loop. This prevents blocking the
   main application and avoids cross-thread event loop issues.

3. **Clean separation**: Thread lifecycle management is inherently sync (`thread.start()`,
   `thread.join()`), while network I/O is naturally async. Mixing them on one loop creates
   complexity.

This design allows the `Node` to be used in both sync scripts (`asyncio.run()` at entry points)
and async applications (`await` directly) without nested event loop conflicts.

## The Three Async Contexts

### 1. Main Thread (CLI or Script)

The main thread has no event loop running. Use `asyncio.run()` at entry points:

```python
# Correct: one asyncio.run per operation
asyncio.run(node.startup("tcp://0.0.0.0:4000"))
asyncio.run(node.join_network(("192.168.1.100", 5000)))
```

### 2. Daemon Threads (P2P, REST)

Each daemon thread runs its own event loop. Service methods called from FastAPI handlers or P2P
protocol handlers use `await` directly:

```python
# Inside a REST handler or P2P protocol handler -- already in an async context
async def handle(self, request, ...):
    result = await self.service.some_method()
    return result
```

### 3. Processor Worker Threads (SyncNamespace)

Processor code runs in worker threads that need sync interfaces to call async service methods.
The `SyncNamespace` wrapper (`simaas/namespace/sync.py`) handles this by maintaining a
**thread-local event loop**:

```python
# Inside a processor's run() method -- sync context
namespace.dor.search(patterns=["*.json"])  # Sync call, internally uses thread-local loop
```

## Common Pitfalls

### Never Nest `asyncio.run()`

`asyncio.run()` creates a new event loop and runs until complete. Calling it from inside an
already-running loop raises `RuntimeError`. This happens when:

- A service method (async) calls `asyncio.run()` instead of `await`
- A REST handler tries to use `asyncio.run()`

```python
# WRONG: nested asyncio.run()
async def handle_request(self, ...):
    result = asyncio.run(self.service.method())  # RuntimeError!

# CORRECT: use await
async def handle_request(self, ...):
    result = await self.service.method()
```

### Never Nest `SyncNamespace._run_async()` Calls

The `SyncNamespace` wrapper uses a thread-local event loop. If an async method called through
`_run_async()` itself calls `_run_async()`, the inner call will try to run on an already-busy
loop:

```python
# WRONG: nested _run_async
def method_a(self):
    self._run_async(self._async_a())  # starts loop

async def _async_a(self):
    self._run_async(self._async_b())  # loop already running!

# CORRECT: use await for inner calls
async def _async_a(self):
    await self._async_b()  # stays within the same loop run
```

### Never Block in Async Context

Blocking calls (time.sleep, synchronous I/O, subprocess without asyncio) in an async context
freeze the event loop and block all concurrent operations:

```python
# WRONG: blocking in async context
async def deploy(self):
    time.sleep(5)  # blocks the entire event loop

# CORRECT: use async sleep
async def deploy(self):
    await asyncio.sleep(5)
```

Exception: SQLAlchemy operations are currently sync and run in async methods. This is acceptable
because database operations are fast and low-concurrency. Migrating to async SQLAlchemy is a
potential future improvement.

## When to Use What

| Context | Pattern | Example |
|---------|---------|---------|
| CLI entry point | `asyncio.run()` | `asyncio.run(node.startup(...))` |
| Service layer | `async def` + `await` | `await self.dor.search(...)` |
| REST handler | `async def` + `await` | `await service.submit_job(...)` |
| P2P handler | `async def` + `await` | `await protocol.handle(...)` |
| Processor thread | `SyncNamespace` wrapper | `namespace.dor.search(...)` |
| Test fixtures | `run_coro_safely()` | `run_coro_safely(node.startup(...))` |

## Key Files

| File | Role |
|------|------|
| `simaas/core/async_helpers.py` | `run_coro_safely()` bridge for sync/async boundaries |
| `simaas/namespace/sync.py` | `SyncNamespace` wrapper with thread-local event loops |
| `simaas/p2p/service.py` | P2P daemon thread with its own event loop |
| `simaas/rest/service.py` | REST daemon thread (uvicorn) with its own event loop |
| `simaas/node/base.py` | Orchestrator that starts daemon threads and coordinates async ops |
