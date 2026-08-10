"""e2e invoice send/download tests on the KSeF TEST environment (need network)."""

import time
from datetime import UTC, datetime, timedelta

import pytest

from ksef import Environment, KsefApiError, KsefClient
from ksef.testing import build_test_invoice, generate_test_certificate, random_nip

pytestmark = pytest.mark.e2e


def test_query_invoice_metadata_empty() -> None:
    nip = random_nip()
    certificate, private_key = generate_test_certificate(nip)
    with KsefClient(Environment.TEST) as client:
        client.authenticate_with_certificate(nip, certificate, private_key)
        # Date range spans at most 3 months; Subject1 = issued (sales) invoices.
        page = client.query_invoice_metadata(
            "Subject1",
            date_from=datetime.now(UTC) - timedelta(days=88),
        )
        # A fresh random NIP has no invoices; this checks the flow and parsing.
        assert page.invoices == []
        assert page.has_more is False


def test_send_invoice_and_download() -> None:
    nip = random_nip()
    certificate, private_key = generate_test_certificate(nip)
    with KsefClient(Environment.TEST) as client:
        client.authenticate_with_certificate(nip, certificate, private_key)

        session = client.open_online_session()
        invoice_xml = build_test_invoice(nip, random_nip(), "FV/1/2026")
        invoice_reference = client.send_invoice(session, invoice_xml)
        invoice = client.wait_for_invoice(session, invoice_reference)
        client.close_online_session(session)

        assert invoice.ksef_number
        assert invoice.invoice_number == "FV/1/2026"

        # Fetch the XML by KSeF number (the repository may lag behind a little).
        downloaded = _retry(lambda: client.get_invoice(invoice.ksef_number), timeout=60)
        assert downloaded == invoice_xml

        # The invoice should also show up in the seller's metadata.
        def find_metadata():
            page = client.query_invoice_metadata(
                "Subject1", date_from=datetime.now(UTC) - timedelta(days=1)
            )
            found = [m for m in page.invoices if m.ksef_number == invoice.ksef_number]
            if not found:
                raise KsefApiError(404, "faktury nie ma jeszcze w metadanych")
            return found[0]

        metadata = _retry(find_metadata, timeout=120)
        assert metadata.invoice_number == "FV/1/2026"
        assert metadata.seller.nip == nip
        assert metadata.gross_amount == 123.0
        assert metadata.form_code.system_code == "FA (3)"


def _retry(action, *, timeout: float, interval: float = 2.0):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return action()
        except KsefApiError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(interval)
