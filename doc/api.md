# API Pythona

Pakiety biblioteczne: `ksef` (klient KSeF API 2.0) i `jpk` (FA(3) →
JPK_V7M(3) → bramka e-Dokumenty). CLI to tylko cienka warstwa na tym API.

## Faktury z KSeF → gotowy JPK

```python
from datetime import UTC, date, datetime
from pathlib import Path

from ksef import Environment, KsefClient
from jpk import Taxpayer, build_jpk_v7m, parse_invoice

nip = "1111111111"

with KsefClient(Environment.PROD) as client:
    client.authenticate_with_ksef_token(nip, ksef_token)
    invoices = [
        parse_invoice(
            client.get_invoice(meta.ksef_number),   # XML faktury (bajty)
            ksef_number=meta.ksef_number,           # numer KSeF z metadanych
        )
        for meta in client.iter_invoice_metadata(
            "Subject1",                             # rola sprzedawcy
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 31, 23, 59, 59, tzinfo=UTC),
        )
    ]

jpk_xml = build_jpk_v7m(
    invoices,
    taxpayer=Taxpayer(                              # JDG; spółka: name="..."
        nip=nip,
        email="jan@example.com",
        first_name="Jan",
        last_name="Kowalski",
        birth_date=date(1980, 5, 1),
    ),
    year=2026,
    month=1,
    tax_office_code="0202",
)
Path("JPK_V7M_2026-01.xml").write_bytes(jpk_xml)
```

`parse_invoice` przyjmuje też `exchange_rate=` dla faktur w walucie obcej
bez kursu w `FaWiersz/KursWaluty`. Przypadki, których nie umie poprawnie
rozliczyć (marża, OSS, odwrotne obciążenie, waluta bez kursu…),
`build_jpk_v7m` odrzuca wyjątkiem `JpkError` — zamiast po cichu zbudować
błędny plik.

## Wysyłka JPK do bramki i odbiór UPO

```python
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from jpk import BramkaClient

certificate = x509.load_pem_x509_certificate(Path("cert.pem").read_bytes())
private_key = load_pem_private_key(Path("key.pem").read_bytes(), password=None)

with BramkaClient("prod") as bramka:            # "test" = bramka testowa
    reference = bramka.send_jpk(
        jpk_xml,
        file_name="JPK_V7M_2026-01.xml",
        certificate=certificate,
        private_key=private_key,
    )
    status = bramka.wait_for_processing(reference)

if status.is_accepted:                          # kod 200
    Path("JPK_V7M_2026-01.upo.xml").write_text(status.upo, encoding="utf-8")
else:
    print(status.code, status.description)
```

Osoba fizyczna może zamiast podpisu uwierzytelnić wysyłkę danymi
autoryzującymi (odpowiednik „podpisu kwotą przychodu" z e-Deklaracji):

```python
from datetime import date

from jpk import AuthData

reference = bramka.send_jpk(
    jpk_xml,
    file_name="JPK_V7M_2026-01.xml",
    auth_data=AuthData(
        nip="1111111111",                       # albo pesel="..."
        first_name="Jan",
        last_name="Kowalski",
        birth_date=date(1980, 5, 1),
        revenue="123456.78",  # przychód za rok o dwa lata wcześniejszy
    ),
)
```

## Zabawa na środowisku testowym KSeF

Środowisko TEST wymaga losowych NIP-ów i akceptuje certyfikaty
samopodpisane — `ksef.testing` generuje jedno i drugie, a także minimalną
fakturę FA(3):

```python
from ksef import Environment, KsefClient
from ksef.testing import build_test_invoice, generate_test_certificate, random_nip

nip = random_nip()
certificate, private_key = generate_test_certificate(nip)

with KsefClient(Environment.TEST) as client:
    client.authenticate_with_certificate(nip, certificate, private_key)
    session = client.open_online_session()
    reference = client.send_invoice(
        session, build_test_invoice(nip, random_nip(), "FV/1/2026")
    )
    invoice = client.wait_for_invoice(session, reference)
    client.close_online_session(session)
    print("Numer KSeF:", invoice.ksef_number)
```

Ten sam certyfikat podpisze też wysyłkę JPK na bramkę testową (ma komplet
atrybutów wymaganych przez KSeF i bramkę).
