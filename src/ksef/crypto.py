"""Cryptographic operations required by the KSeF API 2.0."""

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


def encrypt_ksef_token(
    ksef_token: str, timestamp_ms: int, certificate_der: bytes
) -> str:
    """Encrypt a KSeF token the way POST /auth/ksef-token expects it.

    The string ``{token}|{timestampMs}`` is encrypted with RSA-OAEP (SHA-256)
    under the MF public key; the result is returned Base64-encoded.
    """
    payload = f"{ksef_token}|{timestamp_ms}".encode()
    return base64.b64encode(_rsa_oaep_encrypt(payload, certificate_der)).decode()


def generate_symmetric_key() -> tuple[bytes, bytes]:
    """Generate an AES-256 key (32 B) and an initialization vector (16 B)."""
    return os.urandom(32), os.urandom(16)


def encrypt_symmetric_key(key: bytes, certificate_der: bytes) -> str:
    """Encrypt the symmetric key with the MF public key (RSA-OAEP/SHA-256), Base64."""
    return base64.b64encode(_rsa_oaep_encrypt(key, certificate_der)).decode()


def encrypt_aes_256_cbc(data: bytes, key: bytes, iv: bytes) -> bytes:
    """Encrypt data with AES-256-CBC and PKCS#7 padding (session invoice format)."""
    padder = symmetric_padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()
