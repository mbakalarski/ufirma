"""Test e2e CLI ufirma na środowisku testowym KSeF: download → generate (wymaga sieci)."""

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from lxml import etree
from typer.testing import CliRunner

from jpk import JPK_V7M_NAMESPACE
from ksef import Environment, KsefClient
from ksef.testing import build_test_invoice, generate_test_certificate, random_nip
from ufirma.cli import app

pytestmark = pytest.mark.e2e

NS = {"jpk": JPK_V7M_NAMESPACE}
SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "jpk_v7m" / "jpk_v7m.xsd"

runner = CliRunner()


def test_download_and_generate_jpk(tmp_path: Path) -> None:
    nip = random_nip()
    certificate, private_key = generate_test_certificate(nip)

    # Wystaw fakturę sprzedaży na środowisku testowym.
    with KsefClient(Environment.TEST) as client:
        client.authenticate_with_certificate(nip, certificate, private_key)
        session = client.open_online_session()
        invoice_reference = client.send_invoice(
            session, build_test_invoice(nip, random_nip(), "FV/CLI/1/2026")
        )
        invoice = client.wait_for_invoice(session, invoice_reference)
        client.close_online_session(session)

    cert_file = tmp_path / "cert.pem"
    key_file = tmp_path / "key.pem"
    cert_file.write_bytes(certificate.public_bytes(Encoding.PEM))
    key_file.write_bytes(
        private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    )

    today = datetime.now(UTC).date()
    invoices_dir = tmp_path / "faktury"
    downloaded = invoices_dir / f"{invoice.ksef_number}.xml"

    # Metadane potrafią pojawić się z opóźnieniem — ponawiamy pobieranie.
    deadline = time.monotonic() + 120
    while True:
        result = runner.invoke(
            app,
            [
                "ksef",
                "download",
                "--from",
                today.isoformat(),
                "--to",
                today.isoformat(),
                "--nip",
                nip,
                "--cert",
                str(cert_file),
                "--key",
                str(key_file),
                "--env",
                "test",
                "--output-dir",
                str(invoices_dir),
            ],
        )
        assert result.exit_code == 0, result.output
        if downloaded.exists() or time.monotonic() >= deadline:
            break
        time.sleep(2)
    assert downloaded.exists(), result.output
    assert invoice.ksef_number in result.output

    output = tmp_path / "jpk" / f"JPK_V7M_{today.strftime('%Y-%m')}.xml"
    result = runner.invoke(
        app,
        [
            "jpk",
            "generate",
            "--period",
            today.strftime("%Y-%m"),
            "--nip",
            nip,
            "--name",
            "Testowa Firma Sp. z o.o.",
            "--email",
            "firma@example.com",
            "--tax-office",
            "0202",
            "--input-dir",
            str(invoices_dir),
            "--output-dir",
            str(output.parent),
        ],
    )
    assert result.exit_code == 0, result.output

    doc = etree.parse(str(output))
    schema = etree.XMLSchema(etree.parse(str(SCHEMA_PATH)))
    assert schema.validate(doc), schema.error_log
    row = doc.find("jpk:Ewidencja/jpk:SprzedazWiersz", namespaces=NS)
    assert row.findtext("jpk:NrKSeF", namespaces=NS) == invoice.ksef_number
    assert row.findtext("jpk:DowodSprzedazy", namespaces=NS) == "FV/CLI/1/2026"
