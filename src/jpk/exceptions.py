class JpkError(Exception):
    """Błąd budowy dokumentu JPK (np. nieobsługiwany wariant faktury)."""


class BramkaApiError(JpkError):
    """Błąd HTTP zwrócony przez bramkę e-Dokumenty MF."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
