"""
Holds a reference to the running FastAPI event loop so background threads
(watcher, result_consumer) can schedule coroutines onto it.
Must be set once during lifespan startup before any threads that use it start.
"""
import asyncio

_loop: asyncio.AbstractEventLoop | None = None


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def get_loop() -> asyncio.AbstractEventLoop | None:
    return _loop
