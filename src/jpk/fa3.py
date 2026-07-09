"""Parsowanie faktur FA(3) pobranych z KSeF na model danych do budowy JPK."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from lxml import etree

from jpk.exceptions import JpkError

FA3_NAMESPACE = "http://crd.gov.pl/wzor/2025/06/25/13775/"

# Pola kwotowe elementu Fa (podstawy P_13_* i podatek P_14_*, w tym warianty
# *W — podatek przeliczony na PLN dla faktur w walucie obcej).
AMOUNT_FIELDS = (
    "P_13_1",
    "P_14_1",
    "P_14_1W",
    "P_13_2",
    "P_14_2",
    "P_14_2W",
    "P_13_3",
    "P_14_3",
    "P_14_3W",
    "P_13_4",
    "P_14_4",
    "P_14_4W",
    "P_13_5",
    "P_14_5",
    "P_13_6_1",
    "P_13_6_2",
    "P_13_6_3",
    "P_13_7",
    "P_13_8",
    "P_13_9",
    "P_13_10",
    "P_13_11",
)


@dataclass(frozen=True)
class Buyer:
    """Identyfikacja nabywcy (Podmiot2/DaneIdentyfikacyjne) na potrzeby JPK.

    ``tin`` to numer do pola NrKontrahenta (``BRAK`` gdy nabywca bez
    identyfikatora), ``country_code`` trafia do KodKrajuNadaniaTIN.
    """

    tin: str
    name: str
    country_code: str | None = None


@dataclass(frozen=True)
class Fa3Invoice:
    """Faktura sprzedaży FA(3) pobrana z KSeF.

    ``amounts`` trzyma kwoty pod nazwami pól FA(3) (``P_13_1`` = podstawa
    22/23% itd.); pola nieobecne na fakturze nie mają wpisu (metoda
    ``amount()`` zwraca wtedy 0). ``ksef_number`` pochodzi z metadanych KSeF
    (XML faktury go nie zawiera). ``exchange_rate`` to kurs przeliczenia na
    PLN dla faktur w walucie obcej (z ``FaWiersz/KursWaluty`` albo podany
    przy parsowaniu).
    """

    ksef_number: str
    seller_nip: str
    seller_name: str | None
    buyer: Buyer
    invoice_number: str
    issue_date: date
    sale_date: date | None
    currency: str
    invoice_type: str
    amounts: dict[str, Decimal] = field(default_factory=dict)
    exchange_rate: Decimal | None = None

    def amount(self, fa_field: str) -> Decimal:
        """Kwota pola FA(3) (np. ``P_13_1``); 0 gdy pola nie ma na fakturze."""
        return self.amounts.get(fa_field, Decimal("0"))


def _find_text(element: etree._Element, path: str) -> str | None:
    found = element.findtext(path, namespaces={"fa": FA3_NAMESPACE})
    return found.strip() if found is not None else None


def _require_text(element: etree._Element, path: str, what: str) -> str:
    text = _find_text(element, path)
    if not text:
        raise JpkError(f"Faktura FA(3) bez elementu {what}")
    return text


def _parse_buyer(root: etree._Element) -> Buyer:
    dane = root.find(
        "fa:Podmiot2/fa:DaneIdentyfikacyjne", namespaces={"fa": FA3_NAMESPACE}
    )
    if dane is None:
        raise JpkError("Faktura FA(3) bez Podmiot2/DaneIdentyfikacyjne")
    name = _find_text(dane, "fa:Nazwa") or "BRAK"
    nip = _find_text(dane, "fa:NIP")
    if nip:
        return Buyer(tin=nip, name=name)
    vat_ue = _find_text(dane, "fa:NrVatUE")
    if vat_ue:
        return Buyer(tin=vat_ue, name=name, country_code=_find_text(dane, "fa:KodUE"))
    nr_id = _find_text(dane, "fa:NrID")
    if nr_id:
        return Buyer(tin=nr_id, name=name, country_code=_find_text(dane, "fa:KodKraju"))
    return Buyer(tin="BRAK", name=name)


def _parse_exchange_rate(fa: etree._Element) -> Decimal | None:
    """Kurs z wierszy faktury — o ile wszystkie wiersze mają ten sam kurs."""
    rates = {
        Decimal(text.strip())
        for text in fa.xpath(
            "fa:FaWiersz/fa:KursWaluty/text()", namespaces={"fa": FA3_NAMESPACE}
        )
    }
    return rates.pop() if len(rates) == 1 else None


def parse_invoice(
    xml: bytes, ksef_number: str, exchange_rate: Decimal | None = None
) -> Fa3Invoice:
    """Sparsuj XML faktury FA(3) (bajty pobrane przez ``KsefClient.get_invoice``).

    ``ksef_number`` należy wziąć z metadanych (``InvoiceMetadata.ksef_number``)
    lub z wyniku wysyłki — sam XML faktury nie zawiera numeru KSeF.
    ``exchange_rate`` (kurs przeliczenia na PLN, potrzebny przy walucie obcej)
    ma pierwszeństwo przed kursem odczytanym z ``FaWiersz/KursWaluty``.
    """
    root = etree.fromstring(xml)
    if root.tag != f"{{{FA3_NAMESPACE}}}Faktura":
        raise JpkError(f"To nie jest faktura FA(3): element główny {root.tag}")
    fa = root.find("fa:Fa", namespaces={"fa": FA3_NAMESPACE})
    if fa is None:
        raise JpkError("Faktura FA(3) bez elementu Fa")

    amounts: dict[str, Decimal] = {}
    for fa_field in AMOUNT_FIELDS:
        text = _find_text(fa, f"fa:{fa_field}")
        if text is not None:
            amounts[fa_field] = Decimal(text)

    sale_date = _find_text(fa, "fa:P_6")
    return Fa3Invoice(
        ksef_number=ksef_number,
        seller_nip=_require_text(
            root, "fa:Podmiot1/fa:DaneIdentyfikacyjne/fa:NIP", "Podmiot1/NIP"
        ),
        seller_name=_find_text(root, "fa:Podmiot1/fa:DaneIdentyfikacyjne/fa:Nazwa"),
        buyer=_parse_buyer(root),
        invoice_number=_require_text(fa, "fa:P_2", "P_2 (numer faktury)"),
        issue_date=date.fromisoformat(
            _require_text(fa, "fa:P_1", "P_1 (data wystawienia)")
        ),
        sale_date=date.fromisoformat(sale_date) if sale_date else None,
        currency=_require_text(fa, "fa:KodWaluty", "KodWaluty"),
        invoice_type=_require_text(fa, "fa:RodzajFaktury", "RodzajFaktury"),
        amounts=amounts,
        exchange_rate=exchange_rate
        if exchange_rate is not None
        else _parse_exchange_rate(fa),
    )
