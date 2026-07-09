import base64
import hashlib
import io
import zipfile

import pytest
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from jpk import JpkError, SubmissionStatus
from jpk.bramka import (
    INITUPLOAD_NAMESPACE,
    _default_encryption_certificate,
    _encrypt_aes_cbc,
    _zip_single_file,
    build_init_upload,
)
from jpk.bramka import _Part

NS = {"iu": INITUPLOAD_NAMESPACE}


def test_zip_roundtrip() -> None:
    archive = _zip_single_file("jpk.xml", b"<JPK/>")
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        assert zf.namelist() == ["jpk.xml"]
        assert zf.read("jpk.xml") == b"<JPK/>"


def test_encrypt_aes_cbc_roundtrip() -> None:
    key, iv = b"k" * 32, b"i" * 16
    encrypted = _encrypt_aes_cbc(b"dane testowe", key, iv)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(encrypted) + decryptor.finalize()
    unpadder = sym_padding.PKCS7(128).unpadder()
    assert unpadder.update(padded) + unpadder.finalize() == b"dane testowe"


def test_default_encryption_certificates_load() -> None:
    for environment in ("test", "prod"):
        certificate = _default_encryption_certificate(environment)
        assert certificate.public_key().key_size == 2048


def test_build_init_upload_matches_gateway_structure() -> None:
    jpk_xml = b"<JPK>dokument</JPK>"
    part_content = b"zaszyfrowane"
    part = _Part(
        file_name="JPK_V7M_2026-01.xml.zip.001.aes",
        content=part_content,
        md5_b64=base64.b64encode(hashlib.md5(part_content).digest()).decode(),
    )
    root = build_init_upload(
        jpk_xml,
        "JPK_V7M_2026-01.xml",
        [part],
        encrypted_key=b"klucz",
        iv=b"i" * 16,
        system_code="JPK_V7M (3)",
        schema_version="1-0E",
        form_code="JPK_VAT",
    )
    assert root.tag == f"{{{INITUPLOAD_NAMESPACE}}}InitUpload"
    assert root.findtext("iu:DocumentType", namespaces=NS) == "JPK"
    assert root.findtext("iu:Version", namespaces=NS) == "01.02.01.20160617"

    key_el = root.find("iu:EncryptionKey", namespaces=NS)
    assert key_el.attrib == {
        "algorithm": "RSA",
        "mode": "ECB",
        "padding": "PKCS#1",
        "encoding": "Base64",
    }
    assert base64.b64decode(key_el.text) == b"klucz"

    document = root.find("iu:DocumentList/iu:Document", namespaces=NS)
    form = document.find("iu:FormCode", namespaces=NS)
    assert form.text == "JPK_VAT"
    assert form.get("systemCode") == "JPK_V7M (3)"
    assert form.get("schemaVersion") == "1-0E"
    assert document.findtext("iu:FileName", namespaces=NS) == "JPK_V7M_2026-01.xml"
    assert document.findtext("iu:ContentLength", namespaces=NS) == str(len(jpk_xml))
    hash_el = document.find("iu:HashValue", namespaces=NS)
    assert hash_el.get("algorithm") == "SHA-256"
    assert base64.b64decode(hash_el.text) == hashlib.sha256(jpk_xml).digest()

    signatures = document.find("iu:FileSignatureList", namespaces=NS)
    assert signatures.get("filesNumber") == "1"
    split = signatures.find("iu:Packaging/iu:SplitZip", namespaces=NS)
    assert split.get("type") == "split" and split.get("mode") == "zip"
    aes = signatures.find("iu:Encryption/iu:AES", namespaces=NS)
    assert aes.attrib == {
        "size": "256",
        "block": "16",
        "mode": "CBC",
        "padding": "PKCS#7",
    }
    iv_el = aes.find("iu:IV", namespaces=NS)
    assert iv_el.get("bytes") == "16"
    assert base64.b64decode(iv_el.text) == b"i" * 16

    file_signature = signatures.find("iu:FileSignature", namespaces=NS)
    assert file_signature.findtext("iu:OrdinalNumber", namespaces=NS) == "1"
    assert (
        file_signature.findtext("iu:FileName", namespaces=NS)
        == "JPK_V7M_2026-01.xml.zip.001.aes"
    )
    md5_el = file_signature.find("iu:HashValue", namespaces=NS)
    assert md5_el.get("algorithm") == "MD5"
    assert len(md5_el.text) == 24


def test_send_jpk_rejects_bad_file_name() -> None:
    from ksef.testing import generate_test_certificate, random_nip

    from jpk.bramka import BramkaClient

    certificate, private_key = generate_test_certificate(random_nip())
    with BramkaClient("test") as client:
        with pytest.raises(JpkError, match="Nazwa pliku"):
            client.send_jpk(
                b"<JPK/>",
                file_name="zła nazwa!.xml",
                certificate=certificate,
                private_key=private_key,
            )


def test_submission_status_classification() -> None:
    assert SubmissionStatus(code=120, description="").in_progress
    assert SubmissionStatus(code=301, description="").in_progress
    assert SubmissionStatus(code=200, description="").is_accepted
    assert SubmissionStatus(code=300, description="").is_rejected
    assert SubmissionStatus(code=401, description="").is_rejected
