"""Test e2e wysyłki JPK do bramki test-e-dokumenty.mf.gov.pl (wymaga sieci)."""

from datetime import UTC, datetime

import pytest

from jpk import BramkaClient, Taxpayer, build_jpk_v7m
from ksef.testing import generate_test_certificate, random_nip

pytestmark = pytest.mark.e2e


def test_send_jpk_to_test_gateway() -> None:
    nip = random_nip()
    certificate, private_key = generate_test_certificate(nip)
    today = datetime.now(UTC).date()
    jpk_xml = build_jpk_v7m(
        [],
        taxpayer=Taxpayer(
            nip=nip, name="Testowa Firma Sp. z o.o.", email="firma@example.com"
        ),
        year=today.year,
        month=today.month,
        tax_office_code="0202",
    )

    with BramkaClient("test") as client:
        reference_number = client.send_jpk(
            jpk_xml,
            file_name=f"JPK_V7M_{today.strftime('%Y-%m')}.xml",
            certificate=certificate,
            private_key=private_key,
        )
        assert reference_number

        status = client.wait_for_processing(reference_number, poll_timeout=240)

    assert status.is_accepted, (status.code, status.description, status.details)
    assert status.code == 200
    assert status.upo
