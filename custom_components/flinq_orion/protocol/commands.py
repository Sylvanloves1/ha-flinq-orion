"""Serialized request matcher for future validated vendor commands.

No packet transmission is wired into the integration. Retries are opt-in for
idempotent requests and must allocate a fresh network sequence per attempt.
"""

import asyncio
from collections.abc import Awaitable, Callable
from .errors import MeshTimeoutError


class CommandChannel:
    def __init__(self, send: Callable[[bytes], Awaitable[None]]):
        self.send = send
        self.lock = asyncio.Lock()
        self.pending: tuple[Callable[[object], bool], asyncio.Future] | None = None

    def receive(self, response: object) -> bool:
        if self.pending:
            matcher, future = self.pending
            if not future.done() and matcher(response):
                future.set_result(response)
                return True
        return False

    async def request(
        self,
        payload: bytes,
        matcher: Callable[[object], bool],
        *,
        timeout: float = 5,
        retries: int = 0,
        idempotent: bool = False,
    ):
        if not 0 < timeout <= 60 or not 0 <= retries <= 2 or (retries and not idempotent):
            raise ValueError("Invalid retry policy")
        async with self.lock:
            for attempt in range(retries + 1):
                future = asyncio.get_running_loop().create_future()
                self.pending = matcher, future
                try:
                    async with asyncio.timeout(timeout):
                        await self.send(payload)
                        return await future
                except TimeoutError as err:
                    if attempt == retries:
                        raise MeshTimeoutError("No matching response") from err
                finally:
                    self.pending = None
                    if not future.done():
                        future.cancel()
