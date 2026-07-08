from __future__ import annotations

import base64
import hashlib
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

import httpx
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes
from cryptography.x509 import Certificate

from ksef.auth import CONTEXT_NIP, build_auth_token_request, sign_xades
from ksef.crypto import (
    encrypt_aes_256_cbc,
    encrypt_ksef_token,
    encrypt_symmetric_key,
    generate_symmetric_key,
)
from ksef.exceptions import KsefApiError, KsefAuthenticationError, KsefInvoiceError
from ksef.models import (
    AuthChallenge,
    AuthenticationInit,
    AuthStatus,
    AuthTokens,
    EncryptionCertificate,
    InvoiceMetadata,
    InvoiceMetadataPage,
    OnlineSession,
    SessionInvoice,
    TokenInfo,
)

_TOKEN_ENCRYPTION_USAGE = "KsefTokenEncryption"
_SYMMETRIC_KEY_ENCRYPTION_USAGE = "SymmetricKeyEncryption"

FORM_CODE_FA3 = {"systemCode": "FA (3)", "schemaVersion": "1-0E", "value": "FA"}


def _sha256_base64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


class Environment(StrEnum):
    TEST = "https://api-test.ksef.mf.gov.pl/v2"
    DEMO = "https://api-demo.ksef.mf.gov.pl/v2"
    PROD = "https://api.ksef.mf.gov.pl/v2"


class KsefClient:
    """Klient KSeF API 2.0.

    Po udanym uwierzytelnieniu ``access_token`` jest automatycznie dołączany
    jako nagłówek Bearer do kolejnych wywołań.
    """

    def __init__(
        self,
        environment: Environment | str = Environment.TEST,
        *,
        timeout: float = 30.0,
    ) -> None:
        self.access_token: TokenInfo | None = None
        self.refresh_token: TokenInfo | None = None
        self._http = httpx.Client(
            base_url=str(environment),
            timeout=timeout,
            headers={"Accept": "application/json"},
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        content: bytes | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        bearer: str | None = None,
    ) -> httpx.Response:
        request_headers = dict(headers or {})
        token = bearer or (self.access_token.token if self.access_token else None)
        if token:
            request_headers["Authorization"] = f"Bearer {token}"
        response = self._http.request(
            method, path, json=json, content=content, params=params, headers=request_headers
        )
        if response.status_code >= 400:
            raise KsefApiError(response.status_code, response.text)
        return response

    # --- Uwierzytelnianie: operacje elementarne ---

    def fetch_challenge(self) -> AuthChallenge:
        response = self._request("POST", "/auth/challenge")
        return AuthChallenge.from_json(response.json())

    def submit_xades_signature(self, signed_xml: bytes) -> AuthenticationInit:
        response = self._request(
            "POST",
            "/auth/xades-signature",
            content=signed_xml,
            headers={"Content-Type": "application/xml"},
        )
        return AuthenticationInit.from_json(response.json())

    def submit_ksef_token(
        self,
        challenge: str,
        nip: str,
        encrypted_token: str,
        *,
        public_key_id: str | None = None,
    ) -> AuthenticationInit:
        body: dict[str, Any] = {
            "challenge": challenge,
            "contextIdentifier": {"type": "Nip", "value": nip},
            "encryptedToken": encrypted_token,
        }
        if public_key_id is not None:
            body["publicKeyId"] = public_key_id
        response = self._request("POST", "/auth/ksef-token", json=body)
        return AuthenticationInit.from_json(response.json())

    def get_auth_status(self, reference_number: str, authentication_token: str) -> AuthStatus:
        response = self._request(
            "GET", f"/auth/{reference_number}", bearer=authentication_token
        )
        return AuthStatus.from_json(response.json())

    def redeem_tokens(self, authentication_token: str) -> AuthTokens:
        response = self._request("POST", "/auth/token/redeem", bearer=authentication_token)
        tokens = AuthTokens.from_json(response.json())
        self.access_token = tokens.access_token
        self.refresh_token = tokens.refresh_token
        return tokens

    def refresh_access_token(self) -> TokenInfo:
        if self.refresh_token is None:
            raise KsefAuthenticationError("Brak refresh tokena — najpierw się uwierzytelnij")
        response = self._request(
            "POST", "/auth/token/refresh", bearer=self.refresh_token.token
        )
        self.access_token = TokenInfo.from_json(response.json()["accessToken"])
        return self.access_token

    def get_encryption_certificates(self) -> list[EncryptionCertificate]:
        response = self._request("GET", "/security/public-key-certificates")
        return [EncryptionCertificate.from_json(item) for item in response.json()]

    # --- Uwierzytelnianie: pełne przebiegi ---

    def authenticate_with_certificate(
        self,
        nip: str,
        certificate: Certificate,
        private_key: PrivateKeyTypes,
        *,
        poll_interval: float = 1.0,
        poll_timeout: float = 120.0,
    ) -> AuthTokens:
        """Uwierzytelnij się podpisem XAdES w kontekście podanego NIP."""
        challenge = self.fetch_challenge()
        request = build_auth_token_request(challenge.challenge, CONTEXT_NIP, nip)
        signed_xml = sign_xades(request, certificate, private_key)
        init = self.submit_xades_signature(signed_xml)
        self._wait_for_authentication(init, poll_interval, poll_timeout)
        return self.redeem_tokens(init.authentication_token.token)

    def authenticate_with_ksef_token(
        self,
        nip: str,
        ksef_token: str,
        *,
        poll_interval: float = 1.0,
        poll_timeout: float = 120.0,
    ) -> AuthTokens:
        """Uwierzytelnij się tokenem KSeF w kontekście podanego NIP."""
        challenge = self.fetch_challenge()
        certificate = self._pick_encryption_certificate(_TOKEN_ENCRYPTION_USAGE)
        encrypted = encrypt_ksef_token(
            ksef_token, challenge.timestamp_ms, certificate.certificate_der
        )
        init = self.submit_ksef_token(
            challenge.challenge, nip, encrypted, public_key_id=certificate.public_key_id
        )
        self._wait_for_authentication(init, poll_interval, poll_timeout)
        return self.redeem_tokens(init.authentication_token.token)

    # --- Wysyłka faktur: sesja interaktywna ---

    def open_online_session(
        self, form_code: dict[str, str] = FORM_CODE_FA3
    ) -> OnlineSession:
        """Otwórz sesję interaktywną; klucz AES generowany i szyfrowany automatycznie."""
        key, iv = generate_symmetric_key()
        certificate = self._pick_encryption_certificate(_SYMMETRIC_KEY_ENCRYPTION_USAGE)
        response = self._request(
            "POST",
            "/sessions/online",
            json={
                "formCode": form_code,
                "encryption": {
                    "encryptedSymmetricKey": encrypt_symmetric_key(
                        key, certificate.certificate_der
                    ),
                    "initializationVector": base64.b64encode(iv).decode(),
                    "publicKeyId": certificate.public_key_id,
                },
            },
        )
        data = response.json()
        return OnlineSession(
            reference_number=data["referenceNumber"],
            valid_until=datetime.fromisoformat(data["validUntil"]),
            key=key,
            iv=iv,
        )

    def send_invoice(self, session: OnlineSession, invoice_xml: bytes) -> str:
        """Wyślij fakturę w sesji; zwraca numer referencyjny faktury."""
        encrypted = encrypt_aes_256_cbc(invoice_xml, session.key, session.iv)
        response = self._request(
            "POST",
            f"/sessions/online/{session.reference_number}/invoices",
            json={
                "invoiceHash": _sha256_base64(invoice_xml),
                "invoiceSize": len(invoice_xml),
                "encryptedInvoiceHash": _sha256_base64(encrypted),
                "encryptedInvoiceSize": len(encrypted),
                "encryptedInvoiceContent": base64.b64encode(encrypted).decode(),
            },
        )
        return response.json()["referenceNumber"]

    def get_session_invoice(
        self, session_reference: str, invoice_reference: str
    ) -> SessionInvoice:
        response = self._request(
            "GET", f"/sessions/{session_reference}/invoices/{invoice_reference}"
        )
        return SessionInvoice.from_json(response.json())

    def wait_for_invoice(
        self,
        session: OnlineSession,
        invoice_reference: str,
        *,
        poll_interval: float = 1.0,
        poll_timeout: float = 120.0,
    ) -> SessionInvoice:
        """Czekaj, aż faktura dostanie numer KSeF (status 200); ≥300 = odrzucenie."""
        deadline = time.monotonic() + poll_timeout
        while True:
            invoice = self.get_session_invoice(session.reference_number, invoice_reference)
            if invoice.status_code == 200:
                return invoice
            if invoice.status_code >= 300:
                details = "; ".join(invoice.status_details)
                raise KsefInvoiceError(
                    f"Faktura odrzucona (kod {invoice.status_code}): "
                    f"{invoice.status_description} {details}".rstrip()
                )
            if time.monotonic() >= deadline:
                raise KsefInvoiceError(
                    f"Przekroczono czas oczekiwania ({poll_timeout}s) na przetworzenie "
                    f"faktury (ostatni status: {invoice.status_code} "
                    f"{invoice.status_description})"
                )
            time.sleep(poll_interval)

    def close_online_session(self, session: OnlineSession) -> None:
        """Zamknij sesję; KSeF asynchronicznie generuje zbiorcze UPO."""
        self._request("POST", f"/sessions/online/{session.reference_number}/close")

    # --- Pobieranie faktur ---

    def query_invoice_metadata(
        self,
        subject_type: str,
        date_from: datetime,
        date_to: datetime | None = None,
        *,
        date_type: str = "Issue",
        page_offset: int = 0,
        page_size: int = 100,
    ) -> InvoiceMetadataPage:
        """Pobierz stronę metadanych faktur.

        ``subject_type``: rola w fakturze — ``Subject1`` (sprzedawca),
        ``Subject2`` (nabywca), ``Subject3``, ``SubjectAuthorized``.
        ``date_type``: ``Issue``/``Invoicing``/``PermanentStorage``;
        zakres dat maks. 3 miesiące.
        """
        date_range: dict[str, Any] = {
            "dateType": date_type,
            "from": date_from.isoformat(),
        }
        if date_to is not None:
            date_range["to"] = date_to.isoformat()
        response = self._request(
            "POST",
            "/invoices/query/metadata",
            json={"subjectType": subject_type, "dateRange": date_range},
            params={"pageOffset": page_offset, "pageSize": page_size},
        )
        return InvoiceMetadataPage.from_json(response.json())

    def iter_invoice_metadata(
        self,
        subject_type: str,
        date_from: datetime,
        date_to: datetime | None = None,
        *,
        date_type: str = "Issue",
        page_size: int = 100,
    ) -> Iterator[InvoiceMetadata]:
        """Iteruj po metadanych faktur, przechodząc kolejne strony wyników."""
        page_offset = 0
        while True:
            page = self.query_invoice_metadata(
                subject_type,
                date_from,
                date_to,
                date_type=date_type,
                page_offset=page_offset,
                page_size=page_size,
            )
            yield from page.invoices
            if not page.has_more:
                return
            page_offset += 1

    def get_invoice(self, ksef_number: str) -> bytes:
        """Pobierz XML faktury po numerze KSeF."""
        response = self._request(
            "GET",
            f"/invoices/ksef/{ksef_number}",
            headers={"Accept": "application/xml"},
        )
        return response.content

    def _pick_encryption_certificate(self, usage: str) -> EncryptionCertificate:
        now = datetime.now(UTC)
        for certificate in self.get_encryption_certificates():
            if (
                usage in certificate.usage
                and certificate.valid_from <= now <= certificate.valid_to
            ):
                return certificate
        raise KsefAuthenticationError(
            f"Brak ważnego certyfikatu MF do szyfrowania (usage: {usage})"
        )

    def _wait_for_authentication(
        self, init: AuthenticationInit, poll_interval: float, poll_timeout: float
    ) -> AuthStatus:
        deadline = time.monotonic() + poll_timeout
        while True:
            status = self.get_auth_status(
                init.reference_number, init.authentication_token.token
            )
            if status.code == 200:
                return status
            if status.code >= 300:
                details = "; ".join(status.details)
                raise KsefAuthenticationError(
                    f"Uwierzytelnianie nie powiodło się "
                    f"(kod {status.code}): {status.description} {details}".rstrip()
                )
            if time.monotonic() >= deadline:
                raise KsefAuthenticationError(
                    f"Przekroczono czas oczekiwania ({poll_timeout}s) na zakończenie "
                    f"uwierzytelniania (ostatni status: {status.code} {status.description})"
                )
            time.sleep(poll_interval)
