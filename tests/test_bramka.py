import base64
import hashlib
import io
import zipfile
from datetime import date
from importlib.resources import files
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from lxml import etree

from jpk import AuthData, JpkError, SubmissionStatus
from jpk.bramka import (
    INITUPLOAD_NAMESPACE,
    SIG_NAMESPACE,
    _default_encryption_certificate,
    _encrypt_aes_cbc,
    _zip_single_file,
    build_auth_data,
    build_init_upload,
)
from jpk.bramka import _Part

NS = {"iu": INITUPLOAD_NAMESPACE, "sig": SIG_NAMESPACE}
SIG_SCHEMA_PATH = Path(str(files("jpk"))) / "schemas" / "sig" / "sig-2008_v2-0.xsd"


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


def test_build_auth_data_valid_against_schema() -> None:
    xml = build_auth_data(
        AuthData(
            first_name="Jan",
            last_name="Kowalski",
            birth_date=date(1980, 5, 1),
            revenue="85045.5",
            nip="1111111111",
        )
    )
    assert xml.startswith(b'<?xml version="1.0" encoding="utf-8"?>')
    doc = etree.fromstring(xml)
    schema = etree.XMLSchema(etree.parse(str(SIG_SCHEMA_PATH)))
    assert schema.validate(doc), schema.error_log
    assert doc.findtext("sig:NIP", namespaces=NS) == "1111111111"
    assert doc.findtext("sig:ImiePierwsze", namespaces=NS) == "Jan"
    assert doc.findtext("sig:Nazwisko", namespaces=NS) == "Kowalski"
    assert doc.findtext("sig:DataUrodzenia", namespaces=NS) == "1980-05-01"
    assert doc.findtext("sig:Kwota", namespaces=NS) == "85045.50"


def test_build_auth_data_with_pesel_and_zero_revenue() -> None:
    xml = build_auth_data(
        AuthData(
            first_name="Jan",
            last_name="Kowalski",
            birth_date=date(1980, 5, 1),
            revenue=0,
            pesel="80050112345",
        )
    )
    doc = etree.fromstring(xml)
    schema = etree.XMLSchema(etree.parse(str(SIG_SCHEMA_PATH)))
    assert schema.validate(doc), schema.error_log
    assert doc.findtext("sig:PESEL", namespaces=NS) == "80050112345"
    assert doc.findtext("sig:Kwota", namespaces=NS) == "0.00"


def test_auth_data_requires_exactly_one_identifier() -> None:
    common = dict(
        first_name="Jan",
        last_name="Kowalski",
        birth_date=date(1980, 5, 1),
        revenue="0",
    )
    with pytest.raises(JpkError, match="NIP albo PESEL"):
        AuthData(**common)
    with pytest.raises(JpkError, match="NIP albo PESEL"):
        AuthData(**common, nip="1111111111", pesel="80050112345")


def test_build_auth_data_rejects_bad_revenue() -> None:
    auth = AuthData(
        first_name="Jan",
        last_name="Kowalski",
        birth_date=date(1980, 5, 1),
        revenue="dużo",
        nip="1111111111",
    )
    with pytest.raises(JpkError, match="kwota przychodu"):
        build_auth_data(auth)
    with pytest.raises(JpkError, match="ujemna"):
        build_auth_data(
            AuthData(
                first_name="Jan",
                last_name="Kowalski",
                birth_date=date(1980, 5, 1),
                revenue="-1",
                nip="1111111111",
            )
        )


def test_build_init_upload_with_auth_data() -> None:
    part = _Part(file_name="jpk.xml.zip.001.aes", content=b"x", md5_b64="m" * 24)
    key, iv = b"k" * 32, b"i" * 16
    auth_xml = build_auth_data(
        AuthData(
            first_name="Jan",
            last_name="Kowalski",
            birth_date=date(1980, 5, 1),
            revenue="0",
            nip="1111111111",
        )
    )
    encrypted = _encrypt_aes_cbc(auth_xml, key, iv)
    root = build_init_upload(
        b"<JPK/>",
        "JPK_V7M_2026-01.xml",
        [part],
        encrypted_key=b"klucz",
        iv=iv,
        system_code="JPK_V7M (3)",
        schema_version="1-0E",
        form_code="JPK_VAT",
        encrypted_auth_data=encrypted,
    )
    # AuthData is the last InitUpload element (after DocumentList), Base64 ciphertext.
    auth_el = root[-1]
    assert auth_el.tag == f"{{{INITUPLOAD_NAMESPACE}}}AuthData"
    assert base64.b64decode(auth_el.text) == encrypted

    without = build_init_upload(
        b"<JPK/>",
        "JPK_V7M_2026-01.xml",
        [part],
        encrypted_key=b"klucz",
        iv=iv,
        system_code="JPK_V7M (3)",
        schema_version="1-0E",
        form_code="JPK_VAT",
    )
    assert without.find("iu:AuthData", namespaces=NS) is None


def test_send_jpk_requires_exactly_one_auth_method() -> None:
    from ksef.testing import generate_test_certificate, random_nip

    from jpk.bramka import BramkaClient

    certificate, private_key = generate_test_certificate(random_nip())
    auth = AuthData(
        first_name="Jan",
        last_name="Kowalski",
        birth_date=date(1980, 5, 1),
        revenue="0",
        nip="1111111111",
    )
    with BramkaClient("test") as client:
        with pytest.raises(JpkError, match="jedna metoda"):
            client.send_jpk(b"<JPK/>", file_name="jpk_2026.xml")
        with pytest.raises(JpkError, match="jedna metoda"):
            client.send_jpk(
                b"<JPK/>",
                file_name="jpk_2026.xml",
                certificate=certificate,
                private_key=private_key,
                auth_data=auth,
            )
        with pytest.raises(JpkError, match="certyfikatu razem z kluczem"):
            client.send_jpk(
                b"<JPK/>", file_name="jpk_2026.xml", certificate=certificate
            )


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
