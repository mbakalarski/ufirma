# Rozwój projektu

## Zakres (na dziś)

- Tylko **faktury sprzedaży** (podatnik jako `Podmiot1`/sprzedawca)
  i JPK_V7M (rozliczenie miesięczne); rodzaje faktur VAT i KOR.
- Obsługiwane: stawki 23/8/5/0%, zwolnione, NP (w tym art. 100), WDT,
  eksport, waluty obce, nabywcy bez identyfikatora (`BrakID`), podatnik
  JDG i spółka.
- Uwierzytelnienie wysyłki: podpis XAdES (kwalifikowany/zaufany) albo —
  dla osób fizycznych — dane autoryzujące (bez certyfikatu).
- Generowany JPK jest walidowany offline oficjalnym schematem MF
  (`src/jpk/schemas/jpk_v7m/`, dołączanym do paczki na PyPI), a numery
  KSeF faktur trafiają do wierszy ewidencji (wymóg JPK_V7M(3),
  obowiązuje od 1 lutego 2026).

## Środowisko i testy

```bash
git clone https://github.com/mbakalarski/ufirma && cd ufirma
uv sync                       # środowisko (Python 3.14, .venv)
uv run pytest -m "not e2e"    # testy offline
uv run pytest                 # wszystkie, z e2e na środowiskach testowych MF
                              # (wymagają sieci; samowystarczalne, bez konfiguracji)
```

Paczka deklaruje `requires-python = ">=3.11"` (3.11 to najniższa wersja,
na której kod działa bez zmian — używa `datetime.UTC`, `StrEnum`
i `typing.Self`); rozwój idzie na 3.14, CI sprawdza oba końce zakresu.

Język: komentarze i docstringi w `src/`, `tests/`, `scripts/`
i workflow — **po angielsku**; komunikaty CLI, docstringi komend Typera
(Typer pokazuje je użytkownikowi jako pomoc), README i `doc/` — po
polsku.

## CI

- `ci.yml` — testy offline na macierzy Linux/Windows × Python 3.11/3.14
  oraz budowa paczki przy każdym pushu i PR (Windows jest w macierzy,
  bo tam siedzą użytkownicy końcowi, a strona kodowa konsoli już raz
  wywróciła komunikaty CLI),
- `vendored-schemas.yml` — `check_schemas.py --vendored`, tylko gdy PR
  albo push rusza `src/jpk/schemas/**` lub sam skrypt,
- `mf-checks.yml` (poniedziałki, także ręcznie przez
  `workflow_dispatch`) — `check_schemas.py --upstream` oraz pełne testy
  e2e na środowiskach testowych KSeF i bramki e-Dokumenty,
- `publish.yml` — wydanie na PyPI (opis niżej),
- `renovate.json` — automatyczne PR-y z aktualizacjami zależności
  (uv.lock, GitHub Actions).

## Kontrola schematów MF

`scripts/check_schemas.py` (stdlib, bez zależności) robi dwie różne
rzeczy i warto ich nie mylić.

**`--vendored` — integralność kopii.** Dla każdego pliku
z `src/jpk/schemas/` pobiera XSD spod zaszytego URL-a, wycina z obu
stron `schemaLocation` (lokalne kopie mają je podmienione na ścieżki
obok) i porównuje bajt w bajt. To **nie jest** kontrola aktualności:
wzory w CRWDE są niezmienne (wyróżnik wzoru jest podpisany przez MF),
a schematy typów mają wersję w nazwie pliku, więc te URL-e nie zaczną
serwować czegoś innego. Łapie natomiast schrzanione wendorowanie —
ucięty plik, `StrukturyDanych_v13` w katalogu, gdzie wzór wymaga v12,
schemat „poprawiony", żeby test przeszedł. Rozjazd może wprowadzić
tylko zmiana tych plików, więc sprawdzane są przy PR-ach, które ich
dotykają, a nie co tydzień.

**`--upstream` — czy MF poszło dalej.** Nowy wzór nigdy nie pojawi się
pod istniejącym URL-em, więc ta sekcja patrzy na strony indeksowe:

| Co | Źródło | Znane wartości |
|---|---|---|
| struktury JPK (wzory CRWDE) | `gov.pl/web/kas/struktury-jpk` | ID wzorów rodziny V7M/V7K, w tym `14090` = nasz JPK_V7M(3) |
| schematy faktur KSeF | katalog `faktury/schemy/FA` w `CIRFMF/ksef-docs` (API GitHuba) | `schemat_FA(2)`, `schemat_FA(3)` |
| schemat podpisu bramki | lista struktur XML na `podatki.gov.pl` | `sig-2008_v2-0.xsd` |

Dochodzą do tego dwa źródła, które naprawdę mogą zmienić się w miejscu:
`authv2.xsd` KSeF (stały adres dokumentacji, pilnowany skrótem SHA-256)
i sam plik `sig-2008_v2-0.xsd` (media w CMS-ie MF, można nadpisać pod
tym samym ID) — dlatego jego porównanie bajtowe powtarzane jest też
w tej sekcji.

Cokolwiek spoza znanego zbioru → `NEWER` i kod wyjścia 1. Gdy parser nie
znajdzie na stronie **żadnych** znanych linków → `BROKEN`, też kod 1:
przebudowana strona nie może po cichu raportować „wszystko gra".

Bez flag skrypt wykonuje obie sekcje. W CI wywołanie GitHuba dostaje
`GITHUB_TOKEN`, żeby nie wpaść w limit zapytań anonimowych.

**Uwaga na cotygodniowy harmonogram.** W repozytorium publicznym GitHub
sam wyłącza workflow z `schedule` po **60 dniach bez aktywności**
w repo (włącza się go z powrotem w UI albo przez REST API). Dlatego
`--upstream` jest wywoływany także w `publish.yml`, przed zbudowaniem
paczki: nawet gdyby cron cicho umarł, nie da się wydać wersji ze
schematem, którego MF już nie stosuje. Aktywność podtrzymują też PR-y
Renovate. Jeśli kiedyś okaże się to za mało, zostaje albo zewnętrzny
wyzwalacz `workflow_dispatch` przez API, albo „keepalive" dopychający
pusty commit — świadomie tego nie dodaję, bo commity są Twoje.

**Po publikacji nowego wzoru** cała aktualizacja to jedna edycja
w skrypcie i katalogu schematów: podmienić URL-e w `VENDORED`, ściągnąć
nowe pliki (z podmianą `schemaLocation` na lokalne), dopisać nowe ID do
zbioru znanych — i **wydać nową wersję**, bo użytkownicy walidują kopią
z zainstalowanej paczki.

## Wydanie na PyPI

Publikacja idzie przez **Trusted Publishing** (OIDC) — w sekretach nie
ma tokenu API. Konfiguracja jednorazowa na pypi.org (Publishing →
Add a new pending publisher): właściciel `mbakalarski`, repozytorium
`ufirma`, workflow `publish.yml`, environment `pypi`; analogicznie na
test.pypi.org dla environment `testpypi`.

Wydanie:

1. podnieś `version` w `pyproject.toml` i `uv lock`,
2. próbne wydanie: uruchom workflow „Publish to PyPI" ręcznie
   (`workflow_dispatch`, cel `testpypi`) i sprawdź instalację:
   `uv tool install --index https://test.pypi.org/simple/ ufirma`,
3. opublikuj release w GitHubie z tagiem `vX.Y.Z` — workflow sam
   sprawdzi zgodność tagu z wersją, przepuści testy offline, zbuduje
   paczkę i wypchnie ją na PyPI.

Lokalnie paczkę zbudujesz i obejrzysz przez `uv build`
(`dist/*.whl`, `dist/*.tar.gz`). Wheel zawiera schematy MF
(`jpk/schemas/`) i certyfikaty bramki (`jpk/certs/`) — job `package`
w CI to sprawdza przy każdym pushu.

## Struktura

- `src/ksef/` — klient KSeF API 2.0 (uwierzytelnianie XAdES/token,
  wysyłka FA(3), pobieranie faktur),
- `src/jpk/` — parser FA(3), budowa JPK_V7M(3), klient bramki
  e-Dokumenty (w tym dane autoryzujące),
- `src/jpk/schemas/` — lokalne XSD do walidacji offline (FA(3),
  JPK_V7M(3), SIG-2008), dane pakietu czytane przez
  `importlib.resources`,
- `src/jpk/certs/` — certyfikaty MF do szyfrowania klucza AES,
- `src/ufirma/` — komenda CLI `ufirma` (grupy `ksef` i `jpk`),
- `tests/` — testy offline + e2e (pliki `test_*_e2e.py`).

Szczegóły techniczne i zweryfikowane fakty o API KSeF, JPK_V7M(3)
i bramce e-Dokumenty: [`CLAUDE.md`](../CLAUDE.md).
