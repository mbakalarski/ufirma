# KSeF → JPK

Rozliczenie VAT z faktur w KSeF, od początku do końca w Pythonie:
**pobranie faktur sprzedaży z KSeF → zbudowanie JPK_V7M(3) → wysyłka do
bramki e-Dokumenty MF → odbiór UPO.**

Repozytorium zawiera dwa pakiety (każdy z własną komendą CLI):

- **`ksef`** — klient KSeF API 2.0: uwierzytelnianie (XAdES lub token KSeF),
  wysyłka faktur FA(3), pobieranie metadanych i XML faktur → komenda `ksef`,
- **`jpk`** — budowa JPK_V7M(3) z faktur FA(3) i wysyłka do bramki
  e-Dokumenty z odbiorem UPO → komenda `jpk`.

Wszystkie przebiegi (uwierzytelnianie, wysyłka i pobieranie faktur, budowa
i wysyłka JPK, UPO) są weryfikowane testami e2e na środowiskach testowych MF.

## Po co, skoro jest e-mikrofirma?

Darmowa e-mikrofirma MF nie obsługuje (stan na lipiec 2026) sprzedaży
zagranicznej w walucie obcej — np. faktury dla kontrahenta z USA wystawionej
w USD. Ten projekt tak: podstawy przelicza na PLN kursem z faktury lub podanym
jawnie, podatek bierze z pól `P_14_xW`, a nabywcę bez NIP/VAT-UE (`BrakID`)
wykazuje w ewidencji jako `BRAK` — i taki JPK przechodzi walidację schematem
MF oraz bramkę e-Dokumenty.

## Instalacja

Projekt zarządzany przez [uv](https://docs.astral.sh/uv/), Python 3.14:

```bash
uv sync          # tworzy .venv i instaluje komendy `ksef` oraz `jpk`
```

## CLI — typowy miesiąc rozliczeniowy

```bash
# 1. Pobierz XML faktur sprzedaży za miesiąc (pliki: faktury/<numer-ksef>.xml)
ksef download --from 2026-01-01 --to 2026-01-31 \
    --nip 1111111111 --token "$KSEF_TOKEN" --env prod

# 2. Zbuduj JPK_V7M(3) za okres (plik: jpk/JPK_V7M_2026-01.xml);
#    JDG: --first-name/--last-name/--birth-date, spółka: --name
jpk generate --period 2026-01 --nip 1111111111 \
    --first-name Jan --last-name Kowalski --birth-date 1980-05-01 \
    --email jan@example.com --tax-office 0202

# 3. Wyślij do bramki e-Dokumenty i odbierz UPO
#    (plik UPO: jpk/JPK_V7M_2026-01.upo.xml)
jpk send jpk/JPK_V7M_2026-01.xml --cert cert.pem --key key.pem --env prod

# 3'. Osoba fizyczna (JDG) bez podpisu kwalifikowanego — dane autoryzujące:
#     kwota przychodu za rok podatkowy o dwa lata wcześniejszy (0 gdy brak)
jpk send jpk/JPK_V7M_2026-01.xml --revenue 123456.78 --nip 1111111111 \
    --first-name Jan --last-name Kowalski --birth-date 1980-05-01 --env prod
```

Do tego `jpk send --no-wait` + `jpk status <numer-referencyjny>` (sprawdzenie
wyniku później, `--upo plik.xml` zapisuje UPO) oraz opcje `--input-dir`/
`--output-dir`, gdy katalogi `faktury/` i `jpk/` mają być inne.

Uwierzytelnianie w `ksef download`: token KSeF (`--token`) **albo** certyfikat
z kluczem (`--cert` + `--key`, pliki PEM). Uwierzytelnienie w `jpk send` —
jedna z dwóch metod: `--cert` + `--key` (podpis XAdES; na produkcji
kwalifikowany lub zaufany, na środowisku testowym może być samopodpisany)
**albo** dane autoryzujące, dostępne tylko dla podatnika będącego osobą
fizyczną: `--revenue` (kwota przychodu z zeznania za rok podatkowy o dwa lata
wcześniejszy) razem z `--nip`/`--pesel`, `--first-name`, `--last-name`
i `--birth-date` — bez żadnego certyfikatu.

Zamiast opcji można ustawić zmienne środowiskowe — dla `ksef download`:
`KSEF_NIP`, `KSEF_TOKEN`, `KSEF_CERT`, `KSEF_KEY`, `KSEF_ENV`; dla komend
`jpk`: `JPK_NIP`, `JPK_TAXPAYER_EMAIL`, `JPK_TAX_OFFICE`, `JPK_TAXPAYER_NAME`
(spółka) lub `JPK_TAXPAYER_FIRST_NAME` / `JPK_TAXPAYER_LAST_NAME` /
`JPK_TAXPAYER_BIRTH_DATE` (JDG), `JPK_CERT` / `JPK_KEY` albo `JPK_PESEL` /
`JPK_REVENUE`, oraz `JPK_BRAMKA_ENV`. Kompletny przykład dla JDG:
[`.env.example`](.env.example) (skopiuj do `.env` i załaduj np.
`set -a; source .env; set +a`). Pełna lista opcji: `ksef download --help`,
`jpk --help`.

## API Pythona

### Faktury z KSeF → gotowy JPK

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

`parse_invoice` przyjmuje też `exchange_rate=` dla faktur w walucie obcej bez
kursu w `FaWiersz/KursWaluty`. Przypadki, których nie umie poprawnie rozliczyć
(marża, OSS, odwrotne obciążenie, waluta bez kursu…), `build_jpk_v7m` odrzuca
wyjątkiem `JpkError` — zamiast po cichu zbudować błędny plik.

### Wysyłka JPK do bramki i odbiór UPO

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

### Zabawa na środowisku testowym KSeF

Środowisko TEST wymaga losowych NIP-ów i akceptuje certyfikaty samopodpisane —
`ksef.testing` generuje jedno i drugie, a także minimalną fakturę FA(3):

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

## Środowiska

| System | Test | Produkcja |
|---|---|---|
| KSeF API 2.0 | `api-test.ksef.mf.gov.pl` (losowe NIP-y, certyfikaty samopodpisane) | `api.ksef.mf.gov.pl` (jest też DEMO) |
| Bramka JPK (e-Dokumenty) | `test-e-dokumenty.mf.gov.pl` (podpis samopodpisany OK) | `e-dokumenty.mf.gov.pl` (podpis kwalifikowany/zaufany albo dane autoryzujące) |

## Zakres (na dziś)

- Tylko **faktury sprzedaży** (podatnik jako `Podmiot1`/sprzedawca) i JPK_V7M
  (rozliczenie miesięczne); rodzaje faktur VAT i KOR.
- Obsługiwane: stawki 23/8/5/0%, zwolnione, NP (w tym art. 100), WDT, eksport,
  waluty obce, nabywcy bez identyfikatora (`BrakID`), podatnik JDG i spółka.
- Uwierzytelnienie wysyłki: podpis XAdES (kwalifikowany/zaufany) albo — dla
  osób fizycznych — dane autoryzujące (bez certyfikatu).
- Generowany JPK jest walidowany offline oficjalnym schematem MF
  (`schemas/jpk_v7m/`), a numery KSeF faktur trafiają do wierszy ewidencji
  (wymóg JPK_V7M(3), obowiązuje od 1 lutego 2026).

## Rozwój

```bash
uv sync                       # środowisko
uv run pytest -m "not e2e"    # testy offline
uv run pytest                 # wszystkie, z e2e na środowiskach testowych MF
                              # (wymagają sieci; samowystarczalne, bez konfiguracji)
```

Struktura: `src/ksef/` (klient + CLI), `src/jpk/` (FA(3) → JPK → bramka + CLI),
`schemas/` (lokalne XSD do walidacji offline), `tests/` (offline + e2e).
Szczegóły techniczne i zweryfikowane fakty o API: `CLAUDE.md`.
