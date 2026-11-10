"""The one exception type labnet raises for problems a user can fix.

Every error carries a ``message`` (what went wrong) and an optional ``hint``
(what to do next). The CLI prints both; the web UI returns both as JSON.
"""


class LabnetError(Exception):
    """A problem to show to the user, without a Python traceback."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message}\n  What to do: {self.hint}"
        return self.message
