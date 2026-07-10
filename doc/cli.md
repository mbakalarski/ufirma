# CLI `ufirma` — pełny opis

Komenda `ufirma` ma dwie grupy podkomend: `ufirma ksef` (praca z KSeF)
i `ufirma jpk` (JPK_V7M i bramka e-Dokumenty). Każda opcja ma odpowiednik
w zmiennej środowiskowej — kompletny przykład dla JDG w
[`.env.example`](../.env.example) (skopiuj do `.env` i załaduj:
`set -a; source .env; set +a`; CLI nie czyta `.env` samo). Pełna lista
opcji: `ufirma --help`, `ufirma ksef download --help` itd.

## `ufirma ksef download` — pobranie faktur z KSeF

```bash
ufirma ksef download --from 2026-01-01 --to 2026-01-31 \
    --nip 1111111111 --token "$KSEF_TOKEN" --env prod
```

- Pobiera XML faktur **sprzedaży** (rola `Subject1`; inne role:
  `--subject-type`) wystawionych w zakresie dat, do katalogu
  `--output-dir` (domyślnie `faktury/`); pliki nazywane numerami KSeF.
- Uwierzytelnienie: token KSeF (`--token`) **albo** certyfikat z kluczem
  (`--cert` + `--key`, pliki PEM).
- Środowisko `--env`: `test` | `demo` | `prod`.
- Zmienne środowiskowe: `KSEF_NIP`, `KSEF_TOKEN`, `KSEF_CERT`,
  `KSEF_KEY`, `KSEF_ENV`.

## `ufirma jpk generate` — budowa JPK_V7M(3)

```bash
# JDG (osoba fizyczna):
ufirma jpk generate --period 2026-01 --nip 1111111111 \
    --first-name Jan --last-name Kowalski --birth-date 1980-05-01 \
    --email jan@example.com --tax-office 0202

# spółka: zamiast imienia i nazwiska podaj --name
ufirma jpk generate --period 2026-01 --nip 1111111111 \
    --name "Firma Sp. z o.o." --email biuro@example.com --tax-office 0202
```

- Czyta `*.xml` z `--input-dir` (domyślnie `faktury/`; numer KSeF z nazwy
  pliku), bierze faktury z datą wystawienia (P_1) w podanym okresie,
  zapisuje `JPK_V7M_<okres>.xml` do `--output-dir` (domyślnie `jpk/`).
- `--purpose 2` = korekta (domyślnie 1 = złożenie).
- Waluty obce: podstawa przeliczana kursem z faktury
  (`FaWiersz/KursWaluty`), VAT z pól `P_14_xW`; nabywca bez NIP/VAT-UE
  (`BrakID`) trafia do ewidencji jako `BRAK`.
- Zmienne środowiskowe: `JPK_NIP`, `JPK_TAXPAYER_EMAIL`,
  `JPK_TAX_OFFICE`, `JPK_TAXPAYER_NAME` (spółka) lub
  `JPK_TAXPAYER_FIRST_NAME` / `JPK_TAXPAYER_LAST_NAME` /
  `JPK_TAXPAYER_BIRTH_DATE` (JDG).

## `ufirma jpk send` — wysyłka do bramki e-Dokumenty

Dokładnie **jedna** z dwóch metod uwierzytelnienia:

```bash
# dane autoryzujące — tylko osoba fizyczna, bez certyfikatu:
# kwota przychodu z zeznania za rok o dwa lata wcześniejszy (0 gdy brak)
ufirma jpk send jpk/JPK_V7M_2026-01.xml --revenue 123456.78 \
    --nip 1111111111 --first-name Jan --last-name Kowalski \
    --birth-date 1980-05-01 --env prod

# podpis XAdES — na produkcji certyfikat kwalifikowany lub zaufany,
# na środowisku testowym może być samopodpisany
ufirma jpk send jpk/JPK_V7M_2026-01.xml --cert cert.pem --key key.pem --env prod
```

- Po przyjęciu (status 200) UPO ląduje obok pliku jako
  `<nazwa>.upo.xml`; odrzucenie = komunikat i kod wyjścia 1.
- `--no-wait` nie czeka na wynik — sprawdzisz go później:
  `ufirma jpk status <numer-referencyjny> [--upo plik.xml]`.
- Zamiast `--nip` można podać `--pesel`.
- Środowisko `--env`: `test` | `prod` (domyślnie `test`!).
- Zmienne środowiskowe: `JPK_CERT`/`JPK_KEY` albo
  `JPK_REVENUE`/`JPK_PESEL` (+ dane osobowe jak przy `generate`),
  `JPK_BRAMKA_ENV`.

Uwaga: na produkcji kwota przychodu musi dokładnie zgadzać się
z zeznaniem — inaczej bramka odrzuci dokument (status 419 „Dane
niezgodne z prawdą").

## Środowiska MF

| System | Test | Produkcja |
|---|---|---|
| KSeF API 2.0 | `api-test.ksef.mf.gov.pl` (losowe NIP-y, certyfikaty samopodpisane) | `api.ksef.mf.gov.pl` (jest też DEMO) |
| Bramka JPK (e-Dokumenty) | `test-e-dokumenty.mf.gov.pl` (podpis samopodpisany OK) | `e-dokumenty.mf.gov.pl` (podpis kwalifikowany/zaufany albo dane autoryzujące) |
