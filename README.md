# ufirma

Rozliczenie VAT z faktur w KSeF, od początku do końca:
**pobranie faktur sprzedaży → zbudowanie JPK_V7M(3) → wysyłka do bramki
e-Dokumenty MF → odbiór UPO.** Jedna komenda `ufirma`.

Typowy scenariusz, którego nie obsłuży darmowa e-mikrofirma: **JDG
wystawia w KSeF fakturę dla firmy z USA w USD** (nabywca bez NIP).
`ufirma` przeliczy podstawę na PLN kursem z faktury, weźmie VAT z pól
`P_14_xW`, wykaże nabywcę jako `BRAK` — a JPK wyśle **bez podpisu
kwalifikowanego**, uwierzytelniając danymi autoryzującymi (NIP, imię,
nazwisko, data urodzenia, przychód z zeznania).

## Czego potrzebujesz

- Python 3.11 lub nowszy — najprościej przez
  [uv](https://docs.astral.sh/uv/), który pobierze go sam,
- token KSeF (generowany w Aplikacji Podatnika KSeF, uprawnienie
  odczytu faktur),
- kwota przychodu z zeznania rocznego (np. PIT-36) za rok podatkowy
  **o dwa lata wcześniejszy** niż rok wysyłki (0, gdy nie było).

## Instalacja i konfiguracja

```bash
uv tool install ufirma   # albo: pipx install ufirma / pip install ufirma

curl -O https://raw.githubusercontent.com/mbakalarski/ufirma/main/.env.example
mv .env.example .env     # uzupełnij swoimi danymi (NIP, token, imię…)
set -a; source .env; set +a
```

Windows (PowerShell) — wczytanie `.env` wygląda inaczej,
zob. [doc/cli.md](doc/cli.md#windows-powershell). Instalacja ze źródeł
(do rozwoju projektu) — [doc/rozwoj.md](doc/rozwoj.md).

## Miesiąc rozliczeniowy w trzech krokach

```bash
# 1. Pobierz z KSeF XML faktur sprzedaży za miesiąc → faktury/
ufirma ksef download --from 2026-01-01 --to 2026-01-31

# 2. Zbuduj JPK_V7M(3) za okres → jpk/JPK_V7M_2026-01.xml
ufirma jpk generate --period 2026-01

# 3. Wyślij do bramki e-Dokumenty i odbierz UPO (dane autoryzujące,
#    bez certyfikatu) → jpk/JPK_V7M_2026-01.upo.xml
ufirma jpk send jpk/JPK_V7M_2026-01.xml
```

Reszta (dane podatnika, kwota przychodu, środowiska) idzie ze zmiennych
w `.env`. Gotowy JPK jest sprawdzany oficjalnym schematem MF dołączonym
do paczki, zanim trafi do bramki. Przypadki, których program nie umie
poprawnie rozliczyć (marża, OSS, odwrotne obciążenie, waluta bez
kursu…), kończą się jasnym błędem — zamiast po cichu zbudować zły
plik.

## Więcej

- [doc/cli.md](doc/cli.md) — wszystkie opcje i zmienne środowiskowe,
  spółka, podpis certyfikatem, środowiska testowe MF
- [doc/api.md](doc/api.md) — użycie z Pythona (pakiety `ksef` i `jpk`)
- [doc/rozwoj.md](doc/rozwoj.md) — struktura projektu, testy, zakres

## Licencja i odpowiedzialność

[MIT](LICENSE) — oprogramowanie „TAKIE, JAKIE JEST", **bez gwarancji
i odpowiedzialności autora**. To nie jest porada podatkowa; za poprawność
rozliczeń odpowiada użytkownik, który powinien zweryfikować wygenerowany
JPK przed wysyłką na środowisko produkcyjne MF.
