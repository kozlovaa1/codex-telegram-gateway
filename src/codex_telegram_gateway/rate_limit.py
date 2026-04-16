from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, window_seconds: int, max_events: int) -> None:
        self.window_seconds = window_seconds
        self.max_events = max_events
        self._events: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, user_id: int) -> tuple[bool, int]:
        now = time.monotonic()
        queue = self._events[user_id]
        while queue and queue[0] <= now - self.window_seconds:
            queue.popleft()
        if len(queue) >= self.max_events:
            retry_after = int(max(1, self.window_seconds - (now - queue[0])))
            return False, retry_after
        queue.append(now)
        return True, 0
