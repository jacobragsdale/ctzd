class CliError(Exception):
    """A failure to report to the user and agent; `hint` says what to do next."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class UsageError(CliError):
    """The command was called wrongly; exit status 2."""


class NotFoundError(CliError):
    """The platform has no such app, release, or source."""


class PlatformUnavailableError(CliError):
    """The platform API could not be asked: no network, or it did not accept the login."""
