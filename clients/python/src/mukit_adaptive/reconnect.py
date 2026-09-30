"""Pure reconnect delay. No jitter and no socket I/O."""

from __future__ import annotations

INITIAL_DELAY_MS = 200
MAX_DELAY_MS = 5000
DEFAULT_MAX_ATTEMPTS = 8


def delay_ms_for_attempt(attempt: int) -> int:
    """Return the backoff for a 1-based attempt. Attempt 1 waits 200 ms."""
    if attempt < 1:
        raise ValueError("attempt must be at least 1")
    delay = INITIAL_DELAY_MS * (2 ** (attempt - 1))
    return min(delay, MAX_DELAY_MS)


class ReconnectCounter:
    """Counts abnormal close attempts. Reset only after sockets are open again."""

    def __init__(self, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.max_attempts = max_attempts
        self.attempt = 0

    def next_delay_ms(self) -> int | None:
        """Advance one attempt. ``None`` means the cap is already spent."""
        self.attempt += 1
        if self.attempt > self.max_attempts:
            return None
        return delay_ms_for_attempt(self.attempt)

    def reset(self) -> None:
        self.attempt = 0
