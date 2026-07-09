import dataclasses
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree

from jpk import JpkError, Taxpayer, build_jpk_v7m, parse_invoice
from jpk.v7m import ETD_NAMESPACE, JPK_V7M_NAMESPACE
from ksef.testing import build_test_invoice, random_nip

NS = {"jpk": JPK_V7M_NAMESPACE, "etd": ETD_NAMESPACE}
SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "jpk_v7m" / "jpk_v7m.xsd"
FA3_SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "fa3" / "fa3.xsd"


def ksef_number(nip: str) -> str:
    return f"{nip}-20260315-ABCDEF123456-AB"


def parsed_test_invoice(seller_nip: str, number: str = "FV/1/2026", **kwargs):
    xml = build_test_invoice(seller_nip, random_nip(), number, **kwargs)
    return parse_invoice(xml, ksef_number=ksef_number(seller_nip))


def test_parse_invoice() -> None:
    seller_nip = random_nip()
    buyer_nip = random_nip()
    xml = build_test_invoice(seller_nip, buyer_nip, "FV/42/2026")
    invoice = parse_invoice(xml, ksef_number=ksef_number(seller_nip))

    assert invoice.ksef_number == ksef_number(seller_nip)
    assert invoice.seller_nip == seller_nip
    assert invoice.seller_name == "Testowy Sprzedawca Sp. z o.o."
    assert invoice.buyer.tin == buyer_nip
    assert invoice.buyer.name == "Testowy Nabywca S.A."
    assert invoice.buyer.country_code is None
    assert invoice.invoice_number == "FV/42/2026"
    assert invoice.issue_date == datetime.now(UTC).date()
    assert invoice.sale_date is None
    assert invoice.currency == "PLN"
    assert invoice.invoice_type == "VAT"
    assert invoice.amount("P_13_1") == Decimal("100.00")
    assert invoice.amount("P_14_1") == Decimal("23.00")
    assert invoice.amount("P_13_7") == Decimal("0")


def test_parse_invoice_rejects_non_fa3() -> None:
    with pytest.raises(JpkError, match="FA\\(3\\)"):
        parse_invoice(b"<Faktura/>", ksef_number="1111111111-20260315-ABCDEF123456-AB")


def build_valid_jpk(invoices, taxpayer: Taxpayer) -> etree._Element:
    xml = build_jpk_v7m(
        invoices,
        taxpayer=taxpayer,
        year=2026,
        month=3,
        tax_office_code="0202",
    )
    doc = etree.fromstring(xml)
    schema = etree.XMLSchema(etree.parse(str(SCHEMA_PATH)))
    assert schema.validate(doc), schema.error_log
    return doc


def test_build_jpk_v7m_validates_against_schema() -> None:
    seller_nip = random_nip()
    taxpayer = Taxpayer(
        nip=seller_nip, name="Testowa Firma Sp. z o.o.", email="firma@example.com"
    )

    first = parsed_test_invoice(
        seller_nip, "FV/1/2026", net="100.00", vat="23.00", gross="123.00"
    )
    # Faktura z pozycjami 8% i zwolnionymi (spreparowana na sparsowanym modelu).
    second = dataclasses.replace(
        parsed_test_invoice(seller_nip, "FV/2/2026"),
        amounts={
            "P_13_2": Decimal("200.00"),
            "P_14_2": Decimal("16.00"),
            "P_13_7": Decimal("50.00"),
        },
    )
    doc = build_valid_jpk([first, second], taxpayer)

    rows = doc.findall("jpk:Ewidencja/jpk:SprzedazWiersz", namespaces=NS)
    assert len(rows) == 2
    assert rows[0].findtext("jpk:NrKSeF", namespaces=NS) == ksef_number(seller_nip)
    assert rows[0].findtext("jpk:K_19", namespaces=NS) == "100.00"
    assert rows[0].findtext("jpk:K_20", namespaces=NS) == "23.00"
    assert rows[1].findtext("jpk:K_17", namespaces=NS) == "200.00"
    assert rows[1].findtext("jpk:K_18", namespaces=NS) == "16.00"
    assert rows[1].findtext("jpk:K_10", namespaces=NS) == "50.00"

    ctrl = doc.find("jpk:Ewidencja/jpk:SprzedazCtrl", namespaces=NS)
    assert ctrl.findtext("jpk:LiczbaWierszySprzedazy", namespaces=NS) == "2"
    assert ctrl.findtext("jpk:PodatekNalezny", namespaces=NS) == "39.00"

    pozycje = doc.find("jpk:Deklaracja/jpk:PozycjeSzczegolowe", namespaces=NS)
    assert pozycje.findtext("jpk:P_10", namespaces=NS) == "50"
    assert pozycje.findtext("jpk:P_17", namespaces=NS) == "200"
    assert pozycje.findtext("jpk:P_18", namespaces=NS) == "16"
    assert pozycje.findtext("jpk:P_19", namespaces=NS) == "100"
    assert pozycje.findtext("jpk:P_20", namespaces=NS) == "23"
    assert pozycje.findtext("jpk:P_37", namespaces=NS) == "350"
    assert pozycje.findtext("jpk:P_38", namespaces=NS) == "39"
    assert pozycje.findtext("jpk:P_51", namespaces=NS) == "39"


def test_build_jpk_v7m_rounds_declaration_to_full_pln() -> None:
    seller_nip = random_nip()
    taxpayer = Taxpayer(
        nip=seller_nip, name="Testowa Firma Sp. z o.o.", email="firma@example.com"
    )
    invoice = parsed_test_invoice(seller_nip, net="100.50", vat="23.12", gross="123.62")
    doc = build_valid_jpk([invoice], taxpayer)

    pozycje = doc.find("jpk:Deklaracja/jpk:PozycjeSzczegolowe", namespaces=NS)
    assert pozycje.findtext("jpk:P_19", namespaces=NS) == "101"
    assert pozycje.findtext("jpk:P_20", namespaces=NS) == "23"
    # Ewidencja pozostaje w groszach.
    row = doc.find("jpk:Ewidencja/jpk:SprzedazWiersz", namespaces=NS)
    assert row.findtext("jpk:K_19", namespaces=NS) == "100.50"
    assert row.findtext("jpk:K_20", namespaces=NS) == "23.12"


def test_build_jpk_v7m_without_invoices() -> None:
    taxpayer = Taxpayer(
        nip=random_nip(), name="Testowa Firma Sp. z o.o.", email="f@example.com"
    )
    doc = build_valid_jpk([], taxpayer)
    ctrl = doc.find("jpk:Ewidencja/jpk:SprzedazCtrl", namespaces=NS)
    assert ctrl.findtext("jpk:LiczbaWierszySprzedazy", namespaces=NS) == "0"
    pozycje = doc.find("jpk:Deklaracja/jpk:PozycjeSzczegolowe", namespaces=NS)
    assert pozycje.findtext("jpk:P_51", namespaces=NS) == "0"


def test_build_jpk_v7m_natural_person() -> None:
    seller_nip = random_nip()
    taxpayer = Taxpayer(
        nip=seller_nip,
        email="jan@example.com",
        first_name="Jan",
        last_name="Kowalski",
        birth_date=date(1980, 5, 1),
    )
    doc = build_valid_jpk([parsed_test_invoice(seller_nip)], taxpayer)

    osoba = doc.find("jpk:Podmiot1/jpk:OsobaFizyczna", namespaces=NS)
    assert osoba is not None
    assert doc.find("jpk:Podmiot1/jpk:OsobaNiefizyczna", namespaces=NS) is None
    # Pola identyfikatora osoby fizycznej są w namespace etd (StrukturyDanych).
    assert osoba.findtext("etd:NIP", namespaces=NS) == seller_nip
    assert osoba.findtext("etd:ImiePierwsze", namespaces=NS) == "Jan"
    assert osoba.findtext("etd:Nazwisko", namespaces=NS) == "Kowalski"
    assert osoba.findtext("etd:DataUrodzenia", namespaces=NS) == "1980-05-01"
    assert osoba.findtext("jpk:Email", namespaces=NS) == "jan@example.com"


def test_taxpayer_requires_company_name_or_full_natural_person() -> None:
    with pytest.raises(JpkError, match="osoby fizycznej"):
        Taxpayer(nip=random_nip(), email="f@example.com")
    with pytest.raises(JpkError, match="osoby fizycznej"):
        Taxpayer(
            nip=random_nip(),
            email="f@example.com",
            first_name="Jan",
            last_name="Kowalski",
        )
    with pytest.raises(JpkError, match="nie oba naraz"):
        Taxpayer(
            nip=random_nip(),
            email="f@example.com",
            name="Firma",
            first_name="Jan",
            last_name="Kowalski",
            birth_date=date(1980, 5, 1),
        )


def test_build_jpk_v7m_rejects_foreign_seller() -> None:
    taxpayer = Taxpayer(nip=random_nip(), name="Inna Firma", email="f@example.com")
    invoice = parsed_test_invoice(random_nip())
    with pytest.raises(JpkError, match="NIP sprzedawcy"):
        build_jpk_v7m(
            [invoice], taxpayer=taxpayer, year=2026, month=3, tax_office_code="0202"
        )


def test_parse_invoice_foreign_currency_and_no_buyer_id() -> None:
    seller_nip = random_nip()
    xml = build_test_invoice(
        seller_nip,
        None,
        "FV/USD/1/2026",
        net="100.00",
        vat="23.00",
        gross="123.00",
        currency="USD",
        vat_pln="84.00",
        exchange_rate="3.6500",
        buyer_country="US",
    )
    fa3_schema = etree.XMLSchema(etree.parse(str(FA3_SCHEMA_PATH)))
    fa3_schema.assertValid(etree.fromstring(xml))

    invoice = parse_invoice(xml, ksef_number=ksef_number(seller_nip))
    assert invoice.currency == "USD"
    assert invoice.exchange_rate == Decimal("3.6500")
    assert invoice.amount("P_14_1W") == Decimal("84.00")
    assert invoice.buyer.tin == "BRAK"
    assert invoice.buyer.country_code is None


def test_build_jpk_v7m_foreign_currency_converts_to_pln() -> None:
    seller_nip = random_nip()
    taxpayer = Taxpayer(nip=seller_nip, name="Testowa Firma", email="f@example.com")
    xml = build_test_invoice(
        seller_nip,
        None,
        "FV/USD/1/2026",
        currency="USD",
        vat_pln="84.00",
        exchange_rate="3.6500",
        buyer_country="US",
    )
    invoice = parse_invoice(xml, ksef_number=ksef_number(seller_nip))
    doc = build_valid_jpk([invoice], taxpayer)

    row = doc.find("jpk:Ewidencja/jpk:SprzedazWiersz", namespaces=NS)
    # Kontrahent bez identyfikatora: NrKontrahenta=BRAK, bez KodKrajuNadaniaTIN.
    assert row.find("jpk:KodKrajuNadaniaTIN", namespaces=NS) is None
    assert row.findtext("jpk:NrKontrahenta", namespaces=NS) == "BRAK"
    # Podstawa przeliczona kursem (100.00 × 3.65), podatek z P_14_1W.
    assert row.findtext("jpk:K_19", namespaces=NS) == "365.00"
    assert row.findtext("jpk:K_20", namespaces=NS) == "84.00"

    pozycje = doc.find("jpk:Deklaracja/jpk:PozycjeSzczegolowe", namespaces=NS)
    assert pozycje.findtext("jpk:P_19", namespaces=NS) == "365"
    assert pozycje.findtext("jpk:P_20", namespaces=NS) == "84"


def test_build_jpk_v7m_foreign_currency_without_vat_pln_field() -> None:
    # Sprzedaż NP (poza terytorium kraju) w USD — bez pól P_14_xW; podstawa
    # i (zerowy) podatek przeliczane kursem podanym jawnie.
    seller_nip = random_nip()
    taxpayer = Taxpayer(nip=seller_nip, name="Testowa Firma", email="f@example.com")
    invoice = dataclasses.replace(
        parse_invoice(
            build_test_invoice(seller_nip, None, "FV/USD/2/2026", currency="USD"),
            ksef_number=ksef_number(seller_nip),
            exchange_rate=Decimal("3.6500"),
        ),
        amounts={"P_13_8": Decimal("100.00")},
    )
    doc = build_valid_jpk([invoice], taxpayer)

    row = doc.find("jpk:Ewidencja/jpk:SprzedazWiersz", namespaces=NS)
    assert row.findtext("jpk:K_11", namespaces=NS) == "365.00"
    pozycje = doc.find("jpk:Deklaracja/jpk:PozycjeSzczegolowe", namespaces=NS)
    assert pozycje.findtext("jpk:P_11", namespaces=NS) == "365"


def test_build_jpk_v7m_rejects_foreign_currency_without_rate() -> None:
    seller_nip = random_nip()
    taxpayer = Taxpayer(nip=seller_nip, name="Testowa Firma", email="f@example.com")
    invoice = dataclasses.replace(parsed_test_invoice(seller_nip), currency="EUR")
    with pytest.raises(JpkError, match="bez kursu"):
        build_jpk_v7m(
            [invoice], taxpayer=taxpayer, year=2026, month=3, tax_office_code="0202"
        )


def test_build_jpk_v7m_rejects_unsupported_fa_fields() -> None:
    seller_nip = random_nip()
    taxpayer = Taxpayer(nip=seller_nip, name="Testowa Firma", email="f@example.com")
    invoice = dataclasses.replace(
        parsed_test_invoice(seller_nip), amounts={"P_13_11": Decimal("100.00")}
    )
    with pytest.raises(JpkError, match="P_13_11"):
        build_jpk_v7m(
            [invoice], taxpayer=taxpayer, year=2026, month=3, tax_office_code="0202"
        )
