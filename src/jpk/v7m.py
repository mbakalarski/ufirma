"""Budowa JPK_V7M(3) — ewidencja sprzedaży i deklaracja z faktur FA(3) z KSeF.

Na razie tylko faktury sprzedaży (podatnik jako Podmiot1). Część zakupowa
ewidencji jest pusta (wymagany ZakupCtrl z zerami).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from lxml import etree

from jpk.exceptions import JpkError
from jpk.fa3 import Fa3Invoice

JPK_V7M_NAMESPACE = "http://crd.gov.pl/wzor/2025/12/19/14090/"
# Typy wspólne MF (StrukturyDanych) — w tym namespace są m.in. pola
# identyfikatora osoby fizycznej (NIP, ImiePierwsze, Nazwisko, DataUrodzenia).
ETD_NAMESPACE = (
    "http://crd.gov.pl/xml/schematy/dziedzinowe/mf/2022/09/13/eD/DefinicjeTypy/"
)

# Pola FA(3), których nie umiemy jeszcze zmapować na kolumny JPK — jeśli
# występują na fakturze z niezerową kwotą, odmawiamy budowy pliku.
_UNSUPPORTED_FA_FIELDS = {
    "P_13_4": "ryczałt dla taksówek (4%)",
    "P_14_4": "ryczałt dla taksówek (4%)",
    "P_14_4W": "ryczałt dla taksówek (4%)",
    "P_13_5": "procedury szczególne (OSS)",
    "P_14_5": "procedury szczególne (OSS)",
    "P_13_10": "odwrotne obciążenie",
    "P_13_11": "procedura marży",
}

_SUPPORTED_INVOICE_TYPES = ("VAT", "KOR")

# Kolumny ewidencji sprzedaży wymagane przez schemat parami (podstawa+podatek).
_K_PAIRS = (("K_15", "K_16"), ("K_17", "K_18"), ("K_19", "K_20"))
_K_SINGLES_BEFORE_PAIRS = ("K_10", "K_11", "K_12", "K_13")
_K_SINGLES_AFTER_PAIRS = ("K_21", "K_22")
_K_VAT_COLUMNS = ("K_16", "K_18", "K_20")

_ZERO = Decimal("0")
_GROSZ = Decimal("0.01")


@dataclass(frozen=True)
class Taxpayer:
    """Podatnik (Podmiot1 JPK).

    Spółka (OsobaNiefizyczna): podaj ``name``. Osoba fizyczna / JDG
    (OsobaFizyczna): podaj ``first_name``, ``last_name`` i ``birth_date``
    (wszystkie trzy wymaga schemat).
    """

    nip: str
    email: str
    name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    birth_date: date | None = None
    phone: str | None = None

    def __post_init__(self) -> None:
        natural_fields = (self.first_name, self.last_name, self.birth_date)
        if self.name is not None and any(f is not None for f in natural_fields):
            raise JpkError(
                "Podatnik: podaj albo name (spółka), albo"
                " first_name/last_name/birth_date (osoba fizyczna) — nie oba naraz"
            )
        if self.name is None and any(f is None for f in natural_fields):
            raise JpkError(
                "Podatnik: dla osoby fizycznej wymagane są first_name, last_name"
                " i birth_date (dla spółki — name)"
            )

    @property
    def is_natural_person(self) -> bool:
        return self.name is None


def _pln_amounts(invoice: Fa3Invoice) -> Callable[[str], Decimal]:
    """Kwoty faktury w PLN: podstawy przeliczone kursem, podatek z pól P_14_xW.

    Dla faktur w walucie obcej podstawy opodatkowania przeliczamy kursem
    (art. 31a ustawy o VAT), a kwoty podatku bierzemy z pól P_14_xW (podatek
    w PLN wykazany na fakturze); gdy pola W brak — również przeliczamy kursem.
    """
    if invoice.currency == "PLN":
        return invoice.amount
    rate = invoice.exchange_rate
    if rate is None:
        raise JpkError(
            f"Faktura {invoice.invoice_number}: waluta {invoice.currency} bez kursu"
            " przeliczenia — brak jednolitego FaWiersz/KursWaluty na fakturze;"
            " podaj exchange_rate w parse_invoice()"
        )

    def amount(fa_field: str) -> Decimal:
        if fa_field.startswith("P_14"):
            vat_pln = invoice.amounts.get(f"{fa_field}W")
            if vat_pln is not None:
                return vat_pln
        return (invoice.amount(fa_field) * rate).quantize(
            _GROSZ, rounding=ROUND_HALF_UP
        )

    return amount


def _sales_columns(invoice: Fa3Invoice) -> dict[str, Decimal]:
    """Zmapuj podstawy/podatek z pól FA(3) na kolumny K ewidencji sprzedaży (w PLN)."""
    for fa_field, description in _UNSUPPORTED_FA_FIELDS.items():
        if invoice.amount(fa_field) != _ZERO:
            raise JpkError(
                f"Faktura {invoice.invoice_number}: pole {fa_field}"
                f" ({description}) nie jest jeszcze obsługiwane"
            )
    a = _pln_amounts(invoice)
    return {
        "K_10": a("P_13_7"),  # sprzedaż zwolniona
        "K_11": a("P_13_8") + a("P_13_9"),  # poza terytorium kraju
        "K_12": a("P_13_9"),  # w tym usługi art. 100 ust. 1 pkt 4
        "K_13": a("P_13_6_1"),  # stawka 0% krajowa
        "K_15": a("P_13_3"),  # stawka 5%
        "K_16": a("P_14_3"),
        "K_17": a("P_13_2"),  # stawka 7/8%
        "K_18": a("P_14_2"),
        "K_19": a("P_13_1"),  # stawka 22/23%
        "K_20": a("P_14_1"),
        "K_21": a("P_13_6_2"),  # WDT
        "K_22": a("P_13_6_3"),  # eksport towarów
    }


def _validate_invoice(invoice: Fa3Invoice, taxpayer: Taxpayer) -> None:
    if invoice.seller_nip != taxpayer.nip:
        raise JpkError(
            f"Faktura {invoice.invoice_number}: NIP sprzedawcy {invoice.seller_nip}"
            f" różny od NIP podatnika {taxpayer.nip} — to nie jest faktura sprzedaży podatnika"
        )
    if invoice.invoice_type not in _SUPPORTED_INVOICE_TYPES:
        raise JpkError(
            f"Faktura {invoice.invoice_number}: RodzajFaktury {invoice.invoice_type}"
            f" nie jest jeszcze obsługiwany (tylko {', '.join(_SUPPORTED_INVOICE_TYPES)})"
        )


def _money(value: Decimal) -> str:
    return str(value.quantize(_GROSZ))


def _round_pln(value: Decimal) -> int:
    """Zaokrąglij do pełnych złotych (końcówki ≥ 50 gr w górę, art. 63 Ordynacji)."""
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _el(
    parent: etree._Element,
    name: str,
    text: str | None = None,
    *,
    namespace: str = JPK_V7M_NAMESPACE,
) -> etree._Element:
    child = etree.SubElement(parent, f"{{{namespace}}}{name}")
    if text is not None:
        child.text = text
    return child


def _build_header(
    root: etree._Element,
    year: int,
    month: int,
    tax_office_code: str,
    purpose: int,
    system_name: str,
    generated_at: datetime,
) -> None:
    naglowek = _el(root, "Naglowek")
    kod = _el(naglowek, "KodFormularza", "JPK_VAT")
    kod.set("kodSystemowy", "JPK_V7M (3)")
    kod.set("wersjaSchemy", "1-0E")
    _el(naglowek, "WariantFormularza", "3")
    _el(
        naglowek,
        "DataWytworzeniaJPK",
        generated_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    _el(naglowek, "NazwaSystemu", system_name)
    _el(naglowek, "CelZlozenia", str(purpose)).set("poz", "P_7")
    _el(naglowek, "KodUrzedu", tax_office_code)
    _el(naglowek, "Rok", str(year))
    _el(naglowek, "Miesiac", str(month))


def _build_taxpayer(root: etree._Element, taxpayer: Taxpayer) -> None:
    podmiot = _el(root, "Podmiot1")
    podmiot.set("rola", "Podatnik")
    if taxpayer.is_natural_person:
        osoba = _el(podmiot, "OsobaFizyczna")
        _el(osoba, "NIP", taxpayer.nip, namespace=ETD_NAMESPACE)
        _el(osoba, "ImiePierwsze", taxpayer.first_name, namespace=ETD_NAMESPACE)
        _el(osoba, "Nazwisko", taxpayer.last_name, namespace=ETD_NAMESPACE)
        _el(
            osoba,
            "DataUrodzenia",
            taxpayer.birth_date.isoformat(),  # type: ignore[union-attr]
            namespace=ETD_NAMESPACE,
        )
    else:
        osoba = _el(podmiot, "OsobaNiefizyczna")
        _el(osoba, "NIP", taxpayer.nip)
        _el(osoba, "PelnaNazwa", taxpayer.name)
    _el(osoba, "Email", taxpayer.email)
    if taxpayer.phone:
        _el(osoba, "Telefon", taxpayer.phone)


def _build_declaration(root: etree._Element, totals: dict[str, Decimal]) -> None:
    """Deklaracja VAT-7: podstawy/podatek w pełnych złotych z sum kolumn K."""
    deklaracja = _el(root, "Deklaracja")
    naglowek = _el(deklaracja, "Naglowek")
    kod = _el(naglowek, "KodFormularzaDekl", "VAT-7")
    kod.set("kodSystemowy", "VAT-7 (23)")
    kod.set("kodPodatku", "VAT")
    kod.set("rodzajZobowiazania", "Z")
    kod.set("wersjaSchemy", "1-0E")
    _el(naglowek, "WariantFormularzaDekl", "23")

    p = {
        "P_10": _round_pln(totals["K_10"]),
        "P_11": _round_pln(totals["K_11"]),
        "P_12": _round_pln(totals["K_12"]),
        "P_13": _round_pln(totals["K_13"]),
        "P_15": _round_pln(totals["K_15"]),
        "P_16": _round_pln(totals["K_16"]),
        "P_17": _round_pln(totals["K_17"]),
        "P_18": _round_pln(totals["K_18"]),
        "P_19": _round_pln(totals["K_19"]),
        "P_20": _round_pln(totals["K_20"]),
        "P_21": _round_pln(totals["K_21"]),
        "P_22": _round_pln(totals["K_22"]),
    }
    p["P_37"] = (
        p["P_10"]
        + p["P_11"]
        + p["P_13"]
        + p["P_15"]
        + p["P_17"]
        + p["P_19"]
        + p["P_21"]
        + p["P_22"]
    )
    p["P_38"] = p["P_16"] + p["P_18"] + p["P_20"]
    if p["P_38"] < 0:
        raise JpkError(
            "Ujemny podatek należny za okres (korekty przewyższają sprzedaż)"
            " — przypadek nie jest jeszcze obsługiwany"
        )

    pozycje = _el(deklaracja, "PozycjeSzczegolowe")
    _emit_declaration_positions(pozycje, p)
    # Bez zakupów podatek podlegający wpłacie równa się podatkowi należnemu.
    _el(pozycje, "P_51", str(p["P_38"]))
    _el(deklaracja, "Pouczenia", "1")


def _emit_declaration_positions(pozycje: etree._Element, p: dict[str, int]) -> None:
    # Pary wymagane przez schemat łącznie: (P_11, P_12), (P_13, P_14),
    # (P_15, P_16), (P_17, P_18), (P_19, P_20); P_12 i P_14 są w parze opcjonalne.
    if p["P_10"]:
        _el(pozycje, "P_10", str(p["P_10"]))
    if p["P_11"] or p["P_12"]:
        _el(pozycje, "P_11", str(p["P_11"]))
        if p["P_12"]:
            _el(pozycje, "P_12", str(p["P_12"]))
    if p["P_13"]:
        _el(pozycje, "P_13", str(p["P_13"]))
    for base, tax in (("P_15", "P_16"), ("P_17", "P_18"), ("P_19", "P_20")):
        if p[base] or p[tax]:
            _el(pozycje, base, str(p[base]))
            _el(pozycje, tax, str(p[tax]))
    if p["P_21"]:
        _el(pozycje, "P_21", str(p["P_21"]))
    if p["P_22"]:
        _el(pozycje, "P_22", str(p["P_22"]))
    _el(pozycje, "P_37", str(p["P_37"]))
    _el(pozycje, "P_38", str(p["P_38"]))


def _build_sales_row(
    ewidencja: etree._Element,
    ordinal: int,
    invoice: Fa3Invoice,
    columns: dict[str, Decimal],
) -> None:
    wiersz = _el(ewidencja, "SprzedazWiersz")
    _el(wiersz, "LpSprzedazy", str(ordinal))
    if invoice.buyer.country_code:
        _el(wiersz, "KodKrajuNadaniaTIN", invoice.buyer.country_code)
    _el(wiersz, "NrKontrahenta", invoice.buyer.tin)
    _el(wiersz, "NazwaKontrahenta", invoice.buyer.name)
    _el(wiersz, "DowodSprzedazy", invoice.invoice_number)
    _el(wiersz, "DataWystawienia", invoice.issue_date.isoformat())
    if invoice.sale_date is not None:
        _el(wiersz, "DataSprzedazy", invoice.sale_date.isoformat())
    _el(wiersz, "NrKSeF", invoice.ksef_number)
    for name in _K_SINGLES_BEFORE_PAIRS:
        if columns[name] != _ZERO:
            _el(wiersz, name, _money(columns[name]))
    for base, tax in _K_PAIRS:
        if columns[base] != _ZERO or columns[tax] != _ZERO:
            _el(wiersz, base, _money(columns[base]))
            _el(wiersz, tax, _money(columns[tax]))
    for name in _K_SINGLES_AFTER_PAIRS:
        if columns[name] != _ZERO:
            _el(wiersz, name, _money(columns[name]))


def build_jpk_v7m(
    invoices: Sequence[Fa3Invoice],
    *,
    taxpayer: Taxpayer,
    year: int,
    month: int,
    tax_office_code: str,
    purpose: int = 1,
    system_name: str = "ksef-python",
    generated_at: datetime | None = None,
) -> bytes:
    """Zbuduj JPK_V7M(3) z faktur sprzedaży FA(3) pobranych z KSeF.

    ``purpose``: 1 = złożenie, 2 = korekta. ``tax_office_code`` — czterocyfrowy
    kod urzędu skarbowego (walidowany schematem). Zwraca XML zgodny ze
    schematem ``schemas/jpk_v7m/jpk_v7m.xsd`` (obowiązuje od 2026-02-01).
    """
    rows: list[tuple[Fa3Invoice, dict[str, Decimal]]] = []
    for invoice in invoices:
        _validate_invoice(invoice, taxpayer)
        rows.append((invoice, _sales_columns(invoice)))

    totals = {
        name: sum((columns[name] for _, columns in rows), _ZERO)
        for name in (
            "K_10",
            "K_11",
            "K_12",
            "K_13",
            *(k for pair in _K_PAIRS for k in pair),
            "K_21",
            "K_22",
        )
    }

    root = etree.Element(
        f"{{{JPK_V7M_NAMESPACE}}}JPK",
        nsmap={None: JPK_V7M_NAMESPACE, "etd": ETD_NAMESPACE},
    )
    _build_header(
        root,
        year,
        month,
        tax_office_code,
        purpose,
        system_name,
        generated_at or datetime.now(UTC),
    )
    _build_taxpayer(root, taxpayer)
    _build_declaration(root, totals)

    ewidencja = _el(root, "Ewidencja")
    for ordinal, (invoice, columns) in enumerate(rows, start=1):
        _build_sales_row(ewidencja, ordinal, invoice, columns)
    ctrl = _el(ewidencja, "SprzedazCtrl")
    _el(ctrl, "LiczbaWierszySprzedazy", str(len(rows)))
    _el(ctrl, "PodatekNalezny", _money(sum((totals[k] for k in _K_VAT_COLUMNS), _ZERO)))
    zakup_ctrl = _el(ewidencja, "ZakupCtrl")
    _el(zakup_ctrl, "LiczbaWierszyZakupow", "0")
    _el(zakup_ctrl, "PodatekNaliczony", "0.00")

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")
