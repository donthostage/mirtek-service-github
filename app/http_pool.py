"""One async HTTP pool shared by synchronous API workers, without sharing event loops."""
import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
import threading

import httpx


class SharedHTTPPool:
    def __init__(self, *, transport=None):
        self.transport = transport
        self._lock = threading.Lock()
        self._loop = None
        self._thread = None
        self._client = None
        self._closed = False

    def _serve(self, loop):
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            async def cleanup():
                tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                if self._client is not None:
                    await self._client.aclose()
            loop.run_until_complete(cleanup())
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    async def client(self):
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError('HTTP pool used from another event loop')
        if self._client is None:
            self._client = httpx.AsyncClient(transport=self.transport, trust_env=False,
                follow_redirects=False, limits=httpx.Limits(max_connections=20,
                max_keepalive_connections=10, keepalive_expiry=30))
        return self._client

    def execute(self, operation, *, timeout):
        with self._lock:
            if self._closed:
                raise RuntimeError('HTTP pool is closed')
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(target=self._serve, args=(self._loop,),
                                                name='mirtek-llm-http', daemon=True)
                self._thread.start()
            # run_coroutine_threadsafe preserves the caller's context, including
            # its deadline. Different requests retain independent contexts.
            future = asyncio.run_coroutine_threadsafe(operation(), self._loop)
        try:
            return future.result(timeout=timeout)
        except FutureTimeout:
            future.cancel()
            raise

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread, loop = self._thread, self._loop
            if loop is not None:
                loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=2)
