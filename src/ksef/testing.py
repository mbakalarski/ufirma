"""Narzędzia do pracy ze środowiskiem testowym KSeF.

Środowisko TEST dopuszcza samopodpisane certyfikaty i wymaga używania
losowych NIP-ów (dane nie są izolowane między integratorami).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree

_NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)

FA3_NAMESPACE = "http://crd.gov.pl/wzor/2025/06/25/13775/"


def random_nip() -> str:
    """Wylosuj NIP z poprawną cyfrą kontrolną (do użycia na środowisku testowym)."""
    while True:
        digits = [random.randint(1, 9) for _ in range(3)] + [
            random.randint(0, 9) for _ in range(6)
        ]
        checksum = sum(w * d for w, d in zip(_NIP_WEIGHTS, digits)) % 11
        if checksum != 10:
            return "".join(map(str, digits + [checksum]))


def generate_test_certificate(
    nip: str,
    organization_name: str = "Testowa Firma Sp. z o.o.",
    valid_days: int = 2,
) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    """Wygeneruj samopodpisany certyfikat pieczęci firmowej z NIP-em.

    NIP trafia do pola organizationIdentifier (OID 2.5.4.97) jako ``VATPL-{nip}``
    (wymóg uwierzytelniania KSeF typu certificateSubject) oraz do pola
    serialNumber (OID 2.5.4.5) jako ``TINPL-{nip}`` (wymóg bramki e-Dokumenty
    przy wysyłce JPK). Wyłącznie do środowisk testowych.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "PL"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization_name),
            x509.NameAttribute(NameOID.COMMON_NAME, organization_name),
            x509.NameAttribute(NameOID.ORGANIZATION_IDENTIFIER, f"VATPL-{nip}"),
            x509.NameAttribute(NameOID.SERIAL_NUMBER, f"TINPL-{nip}"),
        ]
    )
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=valid_days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(private_key, hashes.SHA256())
    )
    return certificate, private_key


def build_test_invoice(
    seller_nip: str,
    buyer_nip: str | None,
    invoice_number: str,
    *,
    net: str = "100.00",
    vat: str = "23.00",
    gross: str = "123.00",
    currency: str = "PLN",
    vat_pln: str | None = None,
    exchange_rate: str | None = None,
    buyer_country: str = "PL",
) -> bytes:
    """Zbuduj minimalną fakturę FA(3) VAT (jedna pozycja, stawka 23%).

    ``buyer_nip=None`` daje nabywcę bez identyfikatora podatkowego (BrakID).
    Dla waluty obcej podaj ``vat_pln`` (P_14_1W, podatek przeliczony na PLN)
    i ``exchange_rate`` (KursWaluty w wierszu). Wyłącznie do środowiska
    testowego KSeF.
    """

    def el(
        parent: etree._Element, name: str, text: str | None = None
    ) -> etree._Element:
        child = etree.SubElement(parent, f"{{{FA3_NAMESPACE}}}{name}")
        if text is not None:
            child.text = text
        return child

    today = datetime.now(UTC)
    root = etree.Element(f"{{{FA3_NAMESPACE}}}Faktura", nsmap={None: FA3_NAMESPACE})

    naglowek = el(root, "Naglowek")
    kod = el(naglowek, "KodFormularza", "FA")
    kod.set("kodSystemowy", "FA (3)")
    kod.set("wersjaSchemy", "1-0E")
    el(naglowek, "WariantFormularza", "3")
    el(naglowek, "DataWytworzeniaFa", today.strftime("%Y-%m-%dT%H:%M:%SZ"))
    el(naglowek, "SystemInfo", "ufirma (testy)")

    podmiot1 = el(root, "Podmiot1")
    dane1 = el(podmiot1, "DaneIdentyfikacyjne")
    el(dane1, "NIP", seller_nip)
    el(dane1, "Nazwa", "Testowy Sprzedawca Sp. z o.o.")
    adres1 = el(podmiot1, "Adres")
    el(adres1, "KodKraju", "PL")
    el(adres1, "AdresL1", "ul. Testowa 1, 00-001 Warszawa")

    podmiot2 = el(root, "Podmiot2")
    dane2 = el(podmiot2, "DaneIdentyfikacyjne")
    if buyer_nip is not None:
        el(dane2, "NIP", buyer_nip)
    else:
        el(dane2, "BrakID", "1")
    el(dane2, "Nazwa", "Testowy Nabywca S.A.")
    adres2 = el(podmiot2, "Adres")
    el(adres2, "KodKraju", buyer_country)
    el(adres2, "AdresL1", "ul. Przykładowa 2, 00-002 Kraków")
    el(podmiot2, "JST", "2")
    el(podmiot2, "GV", "2")

    fa = el(root, "Fa")
    el(fa, "KodWaluty", currency)
    el(fa, "P_1", today.date().isoformat())
    el(fa, "P_2", invoice_number)
    el(fa, "P_13_1", net)
    el(fa, "P_14_1", vat)
    if vat_pln is not None:
        el(fa, "P_14_1W", vat_pln)
    el(fa, "P_15", gross)
    adnotacje = el(fa, "Adnotacje")
    el(adnotacje, "P_16", "2")
    el(adnotacje, "P_17", "2")
    el(adnotacje, "P_18", "2")
    el(adnotacje, "P_18A", "2")
    zwolnienie = el(adnotacje, "Zwolnienie")
    el(zwolnienie, "P_19N", "1")
    nst = el(adnotacje, "NoweSrodkiTransportu")
    el(nst, "P_22N", "1")
    el(adnotacje, "P_23", "2")
    pmarzy = el(adnotacje, "PMarzy")
    el(pmarzy, "P_PMarzyN", "1")
    el(fa, "RodzajFaktury", "VAT")
    wiersz = el(fa, "FaWiersz")
    el(wiersz, "NrWierszaFa", "1")
    el(wiersz, "P_7", "Usługa testowa")
    el(wiersz, "P_8A", "szt.")
    el(wiersz, "P_8B", "1")
    el(wiersz, "P_9A", net)
    el(wiersz, "P_11", net)
    el(wiersz, "P_12", "23")
    if exchange_rate is not None:
        el(wiersz, "KursWaluty", exchange_rate)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")
