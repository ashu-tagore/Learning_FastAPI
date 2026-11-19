"""Errors the API reports on purpose, each with a stable code clients can rely on (lesson 4.14).

Routes raise ApiError; app.py turns every error, these included, into one JSON shape.
The code is part of the API's contract: clients branch on it, so it never changes once
published. The message is for people and may be reworded at any time.
"""


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers
