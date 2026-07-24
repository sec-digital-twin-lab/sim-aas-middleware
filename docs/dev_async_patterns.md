# Sync Design + FastAPI Boundary

Application code in the middleware is **fully synchronous**. There is no `asyncio.run()`,
no `await`, and no thread-local event loops in service, DOR, RTI, NodeDB, or P2P code.
Long-running servers (P2P, uvicorn) live in their own daemon threads, but their handlers
call sync service methods directly.

The only `async def` you will see is where a framework mandates it:

- **FastAPI exception handlers and Starlette lifespan hooks** (`simaas/rest/service.py`) —
  Starlette invokes these with `await`; they are thin wrappers that call sync code and
  return.
- **FastAPI `Depends` classes with request-body reads** (e.g. `VerifyAuthorisation` in
  `simaas/rest/auth.py`) — reading the multipart form requires `await request.form()`,
  which is a Starlette API constraint.

Everywhere else, methods are plain `def`.

## Rules of thumb

- **Plugin authors** (DOR, RTI): implement sync `def`. Don't `async def` your service
  methods — the interfaces are sync and the callers do not `await` them.
- **REST endpoint handlers**: `async def` is only required if you need to read the
  request body via `await request.body()` / `await request.form()`. Otherwise write
  plain `def` handlers and let FastAPI schedule them on a thread.
- **P2P protocol `handle` methods**: sync `def`. The P2P server invokes handlers
  serially per connection on daemon threads (`_ConnHandler` in `simaas/p2p/service.py`);
  no async involved.
- **Node lifecycle** (`Node.startup`, `Node.shutdown`, `Node.join_network`,
  `Node.leave_network`, `Node.update_identity`, `Node.shutdown_rti`): sync `def`. Call
  them directly, no `asyncio.run()`.

## Blocking is fine

Sync code can block. DB reads, file I/O, and subprocess calls happen on the calling
thread. This is intentional: the P2P server uses a threading model
(`socketserver.ThreadingTCPServer` in `simaas/p2p/service.py`) and each request handler
runs on its own thread, so a slow handler only blocks that one thread. Uvicorn behaves
the same way for REST endpoints defined with plain `def`.

## History

Application code was async through 4.x. Commit `f6c6a69` stripped async from the
application layer; commit `89e9df7` removed `pytest-asyncio` and the
`simaas.core.async_helpers` module along with any remaining stale references. If you
find `await`, `asyncio.run()`, or `@pytest.mark.asyncio` in application code (outside
the FastAPI/Starlette boundary above), it's a leftover — clean it up.
