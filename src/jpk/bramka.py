"""Submitting JPK to the MF e-Dokumenty gateway (JPK services spec 5.5.1).

Flow: ZIP (DEFLATE) -> split into parts of at most 60 MB -> AES-256-CBC
encryption (the key itself encrypted with RSA/ECB/PKCS#1 under the MF
certificate) -> authenticated ``InitUpload`` to
``POST /api/Storage/InitUploadSigned`` -> ``PUT`` of every part to Azure Blob
-> ``POST /api/Storage/FinishUpload`` -> polling
``GET /api/Storage/Status/{ref}`` (2xx = UPO, 4xx = rejected).

Exactly one technique authenticates the ``InitUpload`` metadata:

- a XAdES-BES signature (qualified or trusted in production; the
  ``test-e-dokumenty.mf.gov.pl`` test environment accepts self-signed ones),
- authorizing data (:class:`AuthData`), available only to taxpayers who are
  natural persons: the ``DaneAutoryzujace`` XML (schema SIG-2008_v2-0),
  encrypted with the same AES key as the JPK file, goes into the ``AuthData``
  element.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from importlib.resources import files

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from lxml import etree

from jpk.exceptions import BramkaApiError, JpkError

INITUPLOAD_NAMESPACE = "http://e-dokumenty.mf.gov.pl"
SIG_NAMESPACE = "http://e-deklaracje.mf.gov.pl/Repozytorium/Definicje/Podpis/"

BRAMKA_TEST = "https://test-e-dokumenty.mf.gov.pl"
BRAMKA_PROD = "https://e-dokumenty.mf.gov.pl"
_ENVIRONMENTS = {"test": BRAMKA_TEST, "prod": BRAMKA_PROD}

_API_VERSION = "01.02.01.20160617"
_PART_SIZE = 60 * 1024 * 1024
_FILE_NAME_PATTERN = re.compile(r"[a-zA-Z0-9_.\-]{5,55}")
# The gateway requires exactly this declaration (lxml defaults to apostrophes).
_XML_DECLARATION = b'<?xml version="1.0" encoding="utf-8"?>'


@dataclass(frozen=True)
class AuthData:
    """Authorizing data for JPK authentication (natural persons only).

    ``revenue`` is the revenue reported in the tax return or annual tax
    computation for the tax year two years before the year of submission
    (``0`` when there was none). Exactly one identifier is required: ``nip``
    or ``pesel``.
    """

    first_name: str
    last_name: str
    birth_date: date
    revenue: Decimal | str
    nip: str | None = None
    pesel: str | None = None

    def __post_init__(self) -> None:
        if (self.nip is None) == (self.pesel is None):
            raise JpkError(
                "Dane autoryzujące wymagają dokładnie jednego identyfikatora:"
                " NIP albo PESEL"
            )


@dataclass(frozen=True)
class SubmissionStatus:
    """Processing status of a document in the gateway (Status method response)."""

    code: int
    description: str
    details: str | None = None
    upo: str | None = None
    timestamp: str | None = None

    @property
    def is_accepted(self) -> bool:
        return 200 <= self.code < 300

    @property
    def is_rejected(self) -> bool:
        return self.code >= 400 or self.code == 300

    @property
    def in_progress(self) -> bool:
        return not (self.is_accepted or self.is_rejected)


@dataclass(frozen=True)
class _Part:
    file_name: str
    content: bytes
    md5_b64: str


def _default_encryption_certificate(environment: str) -> x509.Certificate:
    pem = (files("jpk") / "certs" / f"bramka-{environment}.pem").read_bytes()
    return x509.load_pem_x509_certificate(pem)


def _zip_single_file(file_name: str, content: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(file_name, content)
    return buffer.getvalue()


def _encrypt_aes_cbc(data: bytes, key: bytes, iv: bytes) -> bytes:
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def _sign_xades(
    element: etree._Element, certificate: x509.Certificate, private_key: PrivateKeyTypes
) -> bytes:
    """Sign with enveloped XAdES-BES; XML declaration as the gateway demands."""
    from signxml.xades import XAdESSigner

    cert_pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    signed = XAdESSigner().sign(element, key=key_pem, cert=cert_pem)
    return _XML_DECLARATION + etree.tostring(signed)


def build_auth_data(auth_data: AuthData) -> bytes:
    """Build the ``DaneAutoryzujace`` XML conforming to schema SIG-2008_v2-0."""
    try:
        revenue = Decimal(str(auth_data.revenue)).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise JpkError(
            f"Nieprawidłowa kwota przychodu: {auth_data.revenue!r}"
        ) from exc
    if revenue < 0:
        raise JpkError("Kwota przychodu nie może być ujemna")

    ns = f"{{{SIG_NAMESPACE}}}"
    root = etree.Element(f"{ns}DaneAutoryzujace", nsmap={None: SIG_NAMESPACE})
    if auth_data.nip is not None:
        etree.SubElement(root, f"{ns}NIP").text = auth_data.nip
    else:
        etree.SubElement(root, f"{ns}PESEL").text = auth_data.pesel
    etree.SubElement(root, f"{ns}ImiePierwsze").text = auth_data.first_name
    etree.SubElement(root, f"{ns}Nazwisko").text = auth_data.last_name
    etree.SubElement(root, f"{ns}DataUrodzenia").text = (
        auth_data.birth_date.isoformat()
    )
    etree.SubElement(root, f"{ns}Kwota").text = str(revenue)
    return _XML_DECLARATION + etree.tostring(root)


def build_init_upload(
    jpk_xml: bytes,
    file_name: str,
    parts: list[_Part],
    encrypted_key: bytes,
    iv: bytes,
    *,
    system_code: str,
    schema_version: str,
    form_code: str,
    encrypted_auth_data: bytes | None = None,
) -> etree._Element:
    """Build the (unsigned) InitUpload document conforming to the gateway schema."""
    ns = f"{{{INITUPLOAD_NAMESPACE}}}"
    root = etree.Element(f"{ns}InitUpload", nsmap={None: INITUPLOAD_NAMESPACE})
    etree.SubElement(root, f"{ns}DocumentType").text = "JPK"
    etree.SubElement(root, f"{ns}Version").text = _API_VERSION
    key_el = etree.SubElement(
        root,
        f"{ns}EncryptionKey",
        algorithm="RSA",
        mode="ECB",
        padding="PKCS#1",
        encoding="Base64",
    )
    key_el.text = base64.b64encode(encrypted_key).decode()

    documents = etree.SubElement(root, f"{ns}DocumentList")
    document = etree.SubElement(documents, f"{ns}Document")
    form = etree.SubElement(
        document, f"{ns}FormCode", systemCode=system_code, schemaVersion=schema_version
    )
    form.text = form_code
    etree.SubElement(document, f"{ns}FileName").text = file_name
    etree.SubElement(document, f"{ns}ContentLength").text = str(len(jpk_xml))
    hash_el = etree.SubElement(
        document, f"{ns}HashValue", algorithm="SHA-256", encoding="Base64"
    )
    hash_el.text = base64.b64encode(hashlib.sha256(jpk_xml).digest()).decode()

    signatures = etree.SubElement(
        document, f"{ns}FileSignatureList", filesNumber=str(len(parts))
    )
    packaging = etree.SubElement(signatures, f"{ns}Packaging")
    etree.SubElement(packaging, f"{ns}SplitZip", type="split", mode="zip")
    encryption = etree.SubElement(signatures, f"{ns}Encryption")
    aes = etree.SubElement(
        encryption, f"{ns}AES", size="256", block="16", mode="CBC", padding="PKCS#7"
    )
    iv_el = etree.SubElement(aes, f"{ns}IV", bytes="16", encoding="Base64")
    iv_el.text = base64.b64encode(iv).decode()

    for ordinal, part in enumerate(parts, start=1):
        signature = etree.SubElement(signatures, f"{ns}FileSignature")
        etree.SubElement(signature, f"{ns}OrdinalNumber").text = str(ordinal)
        etree.SubElement(signature, f"{ns}FileName").text = part.file_name
        etree.SubElement(signature, f"{ns}ContentLength").text = str(len(part.content))
        part_hash = etree.SubElement(
            signature, f"{ns}HashValue", algorithm="MD5", encoding="Base64"
        )
        part_hash.text = part.md5_b64

    if encrypted_auth_data is not None:
        auth_el = etree.SubElement(root, f"{ns}AuthData")
        auth_el.text = base64.b64encode(encrypted_auth_data).decode()

    return root


class BramkaClient:
    """Client of the MF e-Dokumenty gateway for submitting JPK files."""

    def __init__(self, environment: str = "test", *, timeout: float = 60.0) -> None:
        if environment not in _ENVIRONMENTS:
            raise JpkError(f"Nieznane środowisko bramki: {environment!r} (test/prod)")
        self._base_url = _ENVIRONMENTS[environment]
        self._encryption_certificate = _default_encryption_certificate(environment)
        self._http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> BramkaClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def send_jpk(
        self,
        jpk_xml: bytes,
        *,
        file_name: str,
        certificate: x509.Certificate | None = None,
        private_key: PrivateKeyTypes | None = None,
        auth_data: AuthData | None = None,
        system_code: str = "JPK_V7M (3)",
        schema_version: str = "1-0E",
        form_code: str = "JPK_VAT",
    ) -> str:
        """Send a JPK document; return the session reference number.

        Exactly one technique authenticates the metadata: ``certificate`` plus
        ``private_key`` for a XAdES signature (qualified or trusted in
        production), or ``auth_data`` for authorizing data (available only to
        taxpayers who are natural persons).
        """
        if (certificate is None) != (private_key is None):
            raise JpkError("Podpis XAdES wymaga certyfikatu razem z kluczem")
        if (certificate is None) == (auth_data is None):
            raise JpkError(
                "Wymagana dokładnie jedna metoda uwierzytelnienia:"
                " certyfikat z kluczem (XAdES) albo dane autoryzujące"
            )
        if not _FILE_NAME_PATTERN.fullmatch(file_name):
            raise JpkError(
                f"Nazwa pliku {file_name!r} niezgodna z wymogiem bramki"
                " ([a-zA-Z0-9_.-], 5-55 znaków)"
            )

        key = os.urandom(32)
        iv = os.urandom(16)
        archive = _zip_single_file(file_name, jpk_xml)
        parts = []
        for index in range(0, len(archive), _PART_SIZE):
            chunk = _encrypt_aes_cbc(archive[index : index + _PART_SIZE], key, iv)
            ordinal = index // _PART_SIZE + 1
            parts.append(
                _Part(
                    file_name=f"{file_name}.zip.{ordinal:03d}.aes",
                    content=chunk,
                    md5_b64=base64.b64encode(hashlib.md5(chunk).digest()).decode(),
                )
            )
        encrypted_key = self._encryption_certificate.public_key().encrypt(
            key, asym_padding.PKCS1v15()
        )

        init_upload = build_init_upload(
            jpk_xml,
            file_name,
            parts,
            encrypted_key,
            iv,
            system_code=system_code,
            schema_version=schema_version,
            form_code=form_code,
            encrypted_auth_data=(
                _encrypt_aes_cbc(build_auth_data(auth_data), key, iv)
                if auth_data is not None
                else None
            ),
        )
        if auth_data is not None:
            body = _XML_DECLARATION + etree.tostring(init_upload)
        else:
            body = _sign_xades(init_upload, certificate, private_key)

        response = self._http.post(
            f"{self._base_url}/api/Storage/InitUploadSigned",
            content=body,
            headers={"Content-Type": "application/xml"},
        )
        _raise_for_error(response, "InitUploadSigned")
        session = response.json()
        reference_number = session["ReferenceNumber"]

        parts_by_name = {part.file_name: part for part in parts}
        blob_names = []
        for request in session["RequestToUploadFileList"]:
            part = parts_by_name[request["FileName"]]
            headers = {h["Key"]: h["Value"] for h in request["HeaderList"]}
            blob_response = self._http.request(
                request["Method"], request["Url"], content=part.content, headers=headers
            )
            if blob_response.status_code not in (200, 201):
                raise BramkaApiError(
                    blob_response.status_code,
                    f"Put Blob ({part.file_name}): {blob_response.text}",
                )
            blob_names.append(request["BlobName"])

        finish_response = self._http.post(
            f"{self._base_url}/api/Storage/FinishUpload",
            json={"ReferenceNumber": reference_number, "AzureBlobNameList": blob_names},
        )
        _raise_for_error(finish_response, "FinishUpload")
        return reference_number

    def get_status(self, reference_number: str) -> SubmissionStatus:
        """Fetch the processing status (including the UPO once accepted)."""
        response = self._http.get(
            f"{self._base_url}/api/Storage/Status/{reference_number}"
        )
        _raise_for_error(response, "Status")
        data = response.json()
        return SubmissionStatus(
            code=int(data["Code"]),
            description=data.get("Description", ""),
            details=data.get("Details"),
            upo=data.get("Upo"),
            timestamp=data.get("Timestamp"),
        )

    def wait_for_processing(
        self,
        reference_number: str,
        *,
        poll_interval: float = 5.0,
        poll_timeout: float = 600.0,
    ) -> SubmissionStatus:
        """Poll the status until processing finishes (2xx/4xx) or the timeout hits."""
        deadline = time.monotonic() + poll_timeout
        while True:
            status = self.get_status(reference_number)
            if not status.in_progress or time.monotonic() >= deadline:
                return status
            time.sleep(poll_interval)


def _raise_for_error(response: httpx.Response, method: str) -> None:
    if response.status_code < 400:
        return
    try:
        payload = response.json()
        message = payload.get("Message", response.text)
        code = payload.get("Code")
    except ValueError:
        message, code = response.text, None
    detail = f"{method}: {message}" + (f" (kod {code})" if code is not None else "")
    raise BramkaApiError(response.status_code, detail)
