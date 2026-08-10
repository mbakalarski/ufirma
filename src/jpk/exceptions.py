class JpkError(Exception):
    """Error while building a JPK document (e.g. an unsupported invoice variant)."""


class BramkaApiError(JpkError):
    """HTTP error returned by the MF e-Dokumenty gateway."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
