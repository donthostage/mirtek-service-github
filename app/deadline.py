"""One monotonic budget shared by model and source calls within a request."""
from contextlib import contextmanager
from contextvars import ContextVar
import time

_deadline = ContextVar('mirtek_request_deadline', default=None)


@contextmanager
def request_budget(seconds):
    deadline = time.monotonic() + seconds
    inherited = _deadline.get()
    token = _deadline.set(min(inherited, deadline) if inherited is not None else deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def operation_budget(maximum):
    """Return remaining time and whether the request deadline limits this operation."""
    deadline = _deadline.get()
    if deadline is None:
        return maximum, False
    remaining = max(0.0, deadline - time.monotonic())
    return min(maximum, remaining), remaining <= maximum
