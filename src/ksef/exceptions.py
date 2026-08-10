class KsefError(Exception):
    """Base exception for the KSeF client."""


class KsefApiError(KsefError):
    """HTTP error returned by the KSeF API."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"KSeF API zwróciło HTTP {status_code}: {body}")
        self.status_code = status_code
        self.body = body


class KsefAuthenticationError(KsefError):
    """Authentication flow did not complete successfully."""


class KsefInvoiceError(KsefError):
    """Invoice was rejected while being processed by KSeF."""
