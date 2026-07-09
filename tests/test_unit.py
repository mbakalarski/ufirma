from datetime import date

from lxml import etree

from ksef.auth import AUTH_NAMESPACE, CONTEXT_NIP, build_auth_token_request
from ksef.models import InvoiceMetadataPage
from ksef.testing import _NIP_WEIGHTS, random_nip


def test_random_nip_is_valid() -> None:
    for _ in range(100):
        nip = random_nip()
        assert len(nip) == 10
        digits = list(map(int, nip))
        assert digits[0] != 0
        checksum = sum(w * d for w, d in zip(_NIP_WEIGHTS, digits[:9])) % 11
        assert checksum == digits[9]


def test_build_auth_token_request() -> None:
    root = build_auth_token_request(
        "20250625-CR-2C1A42B000-159AAE455D-9B", CONTEXT_NIP, "7762811692"
    )
    ns = {"t": AUTH_NAMESPACE}
    assert root.tag == f"{{{AUTH_NAMESPACE}}}AuthTokenRequest"
    assert (
        root.findtext("t:Challenge", namespaces=ns)
        == "20250625-CR-2C1A42B000-159AAE455D-9B"
    )
    assert root.findtext("t:ContextIdentifier/t:Nip", namespaces=ns) == "7762811692"
    assert (
        root.findtext("t:SubjectIdentifierType", namespaces=ns) == "certificateSubject"
    )
    etree.tostring(root)


def test_invoice_metadata_page_from_json() -> None:
    page = InvoiceMetadataPage.from_json(
        {
            "hasMore": False,
            "isTruncated": False,
            "invoices": [
                {
                    "ksefNumber": "1796949259-20260708-010203040506-AB",
                    "invoiceNumber": "FV/7/2026",
                    "issueDate": "2026-07-01",
                    "invoicingDate": "2026-07-01T10:00:00+00:00",
                    "acquisitionDate": "2026-07-01T10:00:01+00:00",
                    "permanentStorageDate": "2026-07-01T10:05:00+00:00",
                    "seller": {"nip": "1796949259", "name": "Testowa Firma Sp. z o.o."},
                    "buyer": {
                        "identifier": {"type": "Nip", "value": "3881292523"},
                        "name": "Nabywca S.A.",
                    },
                    "netAmount": 100.0,
                    "grossAmount": 123.0,
                    "vatAmount": 23.0,
                    "currency": "PLN",
                    "invoicingMode": "Online",
                    "invoiceType": "Vat",
                    "formCode": {
                        "systemCode": "FA (3)",
                        "schemaVersion": "1-0E",
                        "value": "FA",
                    },
                    "isSelfInvoicing": False,
                    "hasAttachment": False,
                    "invoiceHash": "u0PBLbNIQeMgOfHDRIvDW9nOCnj2xkurEP1c3rMSTP4=",
                }
            ],
        }
    )
    assert not page.has_more
    invoice = page.invoices[0]
    assert invoice.ksef_number == "1796949259-20260708-010203040506-AB"
    assert invoice.issue_date == date(2026, 7, 1)
    assert invoice.seller.nip == "1796949259"
    assert invoice.buyer.identifier_type == "Nip"
    assert invoice.buyer.identifier_value == "3881292523"
    assert invoice.gross_amount == 123.0
    assert invoice.form_code.system_code == "FA (3)"
