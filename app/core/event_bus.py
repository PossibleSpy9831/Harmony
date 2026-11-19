"""A small synchronous event bus for coordinating Harmony features."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from threading import RLock
from typing import Any


EventHandler = Callable[[dict[str, Any]], None]


class EventBus:
    def __init__(self):
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._lock = RLock()

    def subscribe(self, event_name: str, handler: EventHandler):
        with self._lock:
            self._handlers[event_name].append(handler)

        def unsubscribe():
            with self._lock:
                handlers = self._handlers.get(event_name, [])
                if handler in handlers:
                    handlers.remove(handler)

        return unsubscribe

    def publish(self, event_name: str, **payload: Any):
        with self._lock:
            handlers = tuple(self._handlers.get(event_name, ()))

        event = dict(payload)
        for handler in handlers:
            handler(event)


event_bus = EventBus()
