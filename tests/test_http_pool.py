import asyncio
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import httpx
import pytest

from app.aitunnel import AITunnelClassifier
from app.deadline import operation_budget, request_budget
from app.http_pool import SharedHTTPPool


def test_two_requests_reuse_one_real_tcp_connection_and_shutdown_closes_it():
    class Server(ThreadingHTTPServer):
        accepted = 0
        def get_request(self):
            socket, address = super().get_request()
            self.accepted += 1
            return socket, address

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')
        def log_message(self, *args):
            pass

    server = Server(('127.0.0.1', 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    pool = SharedHTTPPool()
    async def fetch():
        client = await pool.client()
        response = await client.get(f'http://127.0.0.1:{server.server_port}/', timeout=1)
        return response.text
    try:
        assert pool.execute(fetch, timeout=2) == 'ok'
        assert pool.execute(fetch, timeout=2) == 'ok'
        assert server.accepted == 1
        client = pool._client
        thread = pool._thread
    finally:
        pool.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1)
    assert client.is_closed and not thread.is_alive()
    pool.close()  # Idempotent shutdown.


def test_concurrent_workers_keep_independent_request_budgets():
    pool = SharedHTTPPool()
    gate = threading.Barrier(2)
    def worker(budget):
        with request_budget(budget):
            gate.wait(timeout=1)
            async def read_budget():
                await asyncio.sleep(.01)
                return operation_budget(10)[0]
            return pool.execute(read_budget, timeout=1)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            a = workers.submit(worker, .4)
            b = workers.submit(worker, 2)
            short, long = a.result(), b.result()
        # Windows monotonic clock can keep the same tick across a short sleep;
        # allow rounding without weakening separation of the two contexts.
        assert 0 < short <= .405 and 1.5 < long <= 2.005
    finally:
        pool.close()


def test_pooled_classifier_cancels_stream_at_total_deadline_and_recovers():
    class Stream(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            while True:
                await asyncio.sleep(.01)
                yield b' '
        async def aclose(self):
            self.closed = True

    stream = Stream()
    calls = []
    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, stream=stream)
        return httpx.Response(200, json={'choices':[{'finish_reason':'stop','message':{
            'content':json.dumps({'intents':['promotions'],'reason':None,'uncertain':False})}}]})
    parser = AITunnelClassifier('fake-test-key','test-model', timeout=.07,
        transport=httpx.MockTransport(handle), reuse_connections=True)
    try:
        start = time.monotonic()
        reply = parser.classify('Какие акции?')
        assert reply.reason == 'model_timeout' and time.monotonic()-start < .3
        assert stream.closed
        # The same shared client survives cancellation; no retry was performed.
        assert len(calls) == 1
        assert parser.classify('Какие акции?').intents == ['promotions']
        assert len(calls) == 2
    finally:
        parser.close()


def test_closed_pool_never_runs_new_operation():
    pool = SharedHTTPPool()
    pool.close()
    def forbidden():
        pytest.fail('No operation may be created after shutdown')
    with pytest.raises(RuntimeError):
        pool.execute(forbidden, timeout=1)
