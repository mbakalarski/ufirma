"""Wysyłka JPK do bramki e-Dokumenty MF (spec. interfejsów usług JPK 5.2.0).

Przebieg: ZIP (DEFLATE) → podział na części ≤60 MB → szyfrowanie AES-256-CBC
(klucz szyfrowany RSA/ECB/PKCS#1 certyfikatem MF) → podpisany XAdES-BES
``InitUpload`` na ``POST /api/Storage/InitUploadSigned`` → ``PUT`` części do
Azure Blob → ``POST /api/Storage/FinishUpload`` → polling
``GET /api/Storage/Status/{ref}`` (2xx = UPO, 4xx = odrzucony).

Na środowisku testowym (``test-e-dokumenty.mf.gov.pl``) podpis samopodpisany
jest akceptowany; na produkcji wymagany jest podpis kwalifikowany lub zaufany.
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

BRAMKA_TEST = "https://test-e-dokumenty.mf.gov.pl"
BRAMKA_PROD = "https://e-dokumenty.mf.gov.pl"
_ENVIRONMENTS = {"test": BRAMKA_TEST, "prod": BRAMKA_PROD}

_API_VERSION = "01.02.01.20160617"
_PART_SIZE = 60 * 1024 * 1024
_FILE_NAME_PATTERN = re.compile(r"[a-zA-Z0-9_.\-]{5,55}")


@dataclass(frozen=True)
class SubmissionStatus:
    """Status przetwarzania dokumentu w bramce (odpowiedź metody Status)."""

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
    """Podpisz XAdES-BES (enveloped); deklaracja XML zgodnie z wymogiem bramki."""
    from signxml.xades import XAdESSigner

    cert_pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    signed = XAdESSigner().sign(element, key=key_pem, cert=cert_pem)
    # Bramka wymaga deklaracji dokładnie w postaci <?xml version="1.0" encoding="utf-8"?>.
    return b'<?xml version="1.0" encoding="utf-8"?>' + etree.tostring(signed)


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
) -> etree._Element:
    """Zbuduj (niepodpisany) dokument InitUpload zgodny ze schematem bramki."""
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

    return root


class BramkaClient:
    """Klient bramki e-Dokumenty MF do wysyłki plików JPK."""

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
        certificate: x509.Certificate,
        private_key: PrivateKeyTypes,
        system_code: str = "JPK_V7M (3)",
        schema_version: str = "1-0E",
        form_code: str = "JPK_VAT",
    ) -> str:
        """Wyślij dokument JPK; zwróć numer referencyjny sesji.

        ``certificate``/``private_key`` służą do podpisu XAdES metadanych
        (na produkcji: podpis kwalifikowany).
        """
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
        )
        signed = _sign_xades(init_upload, certificate, private_key)

        response = self._http.post(
            f"{self._base_url}/api/Storage/InitUploadSigned",
            content=signed,
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
        """Pobierz status przetwarzania (z UPO po pozytywnym zakończeniu)."""
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
        """Odpytuj status aż do zakończenia przetwarzania (2xx/4xx) lub timeoutu."""
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
