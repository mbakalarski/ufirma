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
  (`schemas/jpk_v7m/`), a numery KSeF faktur trafiają do wierszy
  ewidencji (wymóg JPK_V7M(3), obowiązuje od 1 lutego 2026).

## Środowisko i testy

```bash
uv sync                       # środowisko (Python 3.14, .venv)
uv run pytest -m "not e2e"    # testy offline
uv run pytest                 # wszystkie, z e2e na środowiskach testowych MF
                              # (wymagają sieci; samowystarczalne, bez konfiguracji)
```

## CI i kontrola okresowa

- `.github/workflows/ci.yml` — testy offline przy każdym pushu i PR,
- `.github/workflows/okresowe.yml` (poniedziałki, także ręcznie przez
  `workflow_dispatch`) — kontrola, czy schematy w `schemas/` są zgodne
  z aktualnie publikowanymi przez MF (`python3 scripts/check_schemas.py`,
  porównanie z pominięciem lokalizowanych `schemaLocation`), oraz pełne
  testy e2e na środowiskach testowych KSeF i bramki e-Dokumenty,
- `renovate.json` — automatyczne PR-y z aktualizacjami zależności
  (uv.lock, GitHub Actions).

## Struktura

- `src/ksef/` — klient KSeF API 2.0 (uwierzytelnianie XAdES/token,
  wysyłka FA(3), pobieranie faktur),
- `src/jpk/` — parser FA(3), budowa JPK_V7M(3), klient bramki
  e-Dokumenty (w tym dane autoryzujące),
- `src/ufirma/` — komenda CLI `ufirma` (grupy `ksef` i `jpk`),
- `schemas/` — lokalne XSD do walidacji offline (FA(3), JPK_V7M(3),
  SIG-2008),
- `tests/` — testy offline + e2e (pliki `test_*_e2e.py`).

Szczegóły techniczne i zweryfikowane fakty o API KSeF, JPK_V7M(3)
i bramce e-Dokumenty: [`CLAUDE.md`](../CLAUDE.md).
