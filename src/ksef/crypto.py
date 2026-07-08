"""Operacje kryptograficzne wymagane przez KSeF API 2.0."""

from __future__ import annotations

import base64
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, padding as symmetric_padding
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def _rsa_oaep_encrypt(payload: bytes, certificate_der: bytes) -> bytes:
    public_key = x509.load_der_x509_certificate(certificate_der).public_key()
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise TypeError("Certyfikat MF do szyfrowania musi zawierać klucz RSA")
    return public_key.encrypt(
        payload,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )


def encrypt_ksef_token(ksef_token: str, timestamp_ms: int, certificate_der: bytes) -> str:
    """Zaszyfruj token KSeF do postaci wymaganej przez POST /auth/ksef-token.

    Szyfrowany jest ciąg ``{token}|{timestampMs}`` algorytmem RSA-OAEP (SHA-256)
    kluczem publicznym MF; wynik zwracany jako Base64.
    """
    payload = f"{ksef_token}|{timestamp_ms}".encode()
    return base64.b64encode(_rsa_oaep_encrypt(payload, certificate_der)).decode()


def generate_symmetric_key() -> tuple[bytes, bytes]:
    """Wygeneruj klucz AES-256 (32 B) i wektor inicjalizujący (16 B)."""
    return os.urandom(32), os.urandom(16)


def encrypt_symmetric_key(key: bytes, certificate_der: bytes) -> str:
    """Zaszyfruj klucz symetryczny kluczem publicznym MF (RSA-OAEP/SHA-256), Base64."""
    return base64.b64encode(_rsa_oaep_encrypt(key, certificate_der)).decode()


def encrypt_aes_256_cbc(data: bytes, key: bytes, iv: bytes) -> bytes:
    """Zaszyfruj dane AES-256-CBC z dopełnieniem PKCS#7 (format faktur w sesji)."""
    padder = symmetric_padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()
