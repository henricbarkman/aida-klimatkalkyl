"""Errors whose message is written for the person using AIda.

Most exceptions must not reach the chat window: "Expecting value: line 1
column 4 (char 3)" is what Sara got in June 2026, and it tells a building
manager nothing. The web layer therefore replaces unknown exception text with a
generic Swedish sentence.

Some failures do have something useful to say ("dela upp projektet i färre
komponenter"). Those raise UserFacingError, and the web layer passes the
message straight through.
"""

from __future__ import annotations


class UserFacingError(RuntimeError):
    """Message is already written for the user. Safe to show verbatim."""

    #: HTTP status the web layer should answer with.
    status_code = 500

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
