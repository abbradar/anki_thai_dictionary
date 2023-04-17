from typing import Any, TypeVar, cast
from collections.abc import Callable
import threading


# Fix this in Python 3.11:
# Ts = TypeVarTuple("Ts")
# T = TypeVar("T")
# def norecurse(*, getter: Callable[[*Ts], Any] = lambda *args: args) -> Callable[[Callable[[*Ts], T]], Callable[[*Ts], T]]:
#     def decorator(wrapped: Callable[[*Ts], T]) -> Callable[[*Ts], T]:
def norecurse(*, getter: Callable = lambda *args: args) -> Callable[[Callable], Callable]:
    def decorator(wrapped: Callable) -> Callable:
        storage = threading.local()
        def f(*args, **kwargs):
            key = getter(*args, **kwargs)
            if key in storage.__dict__:
                raise RuntimeError("Unexpected recursion")
            storage.__dict__[key] = True
            # Fix this in Python 3.11:
            result = cast(Any, wrapped)(*args, **kwargs)
            del storage.__dict__[key]
            return result
        return f
    return decorator
