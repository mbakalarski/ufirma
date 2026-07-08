class KsefError(Exception):
    """Bazowy wyjątek klienta KSeF."""


class KsefApiError(KsefError):
    """Błąd HTTP zwrócony przez API KSeF."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"KSeF API zwróciło HTTP {status_code}: {body}")
        self.status_code = status_code
        self.body = body


class KsefAuthenticationError(KsefError):
    """Proces uwierzytelniania zakończył się niepowodzeniem."""


class KsefInvoiceError(KsefError):
    """Faktura została odrzucona przy przetwarzaniu w KSeF."""
