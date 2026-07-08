# Klient KSeF (Python)

Biblioteka-klient KSeF API 2.0 (Krajowy System e-Faktur). Pakiet `ksef`, układ `src/`, zarządzany przez **uv**. Komunikacja z użytkownikiem po polsku. **Nie używać poleceń git** — commitami zarządza użytkownik.

## Komendy

- `uv sync` — instalacja środowiska (Python 3.14, `.venv`)
- `uv run pytest` — wszystkie testy; testy z markerem `e2e` (pliki `tests/test_*_e2e.py`) uderzają w **żywe środowisko testowe KSeF** i wymagają sieci; są samowystarczalne (losowy NIP + samopodpisany certyfikat, bez env)
- `uv run pytest -m "not e2e"` — tylko testy offline
- `uv add <pkg>` / `uv add --dev <pkg>` — zależności (nie używać pip)

## Struktura

- `src/ksef/client.py` — `KsefClient` (httpx, sync) + `Environment` (TEST/DEMO/PROD); pełne przebiegi `authenticate_with_certificate()` i `authenticate_with_ksef_token()`; po uwierzytelnieniu nagłówek `Bearer` doklejany automatycznie; wysyłka: `open_online_session()` → `send_invoice()` → `wait_for_invoice()` (numer KSeF) → `close_online_session()`; pobieranie: `query_invoice_metadata()` / `iter_invoice_metadata()` (paginacja) i `get_invoice(ksef_number)` (XML)
- `src/ksef/auth.py` — budowa XML `AuthTokenRequest` (zgodnie z `authv2.xsd`) i podpis XAdES-BES (signxml)
- `src/ksef/crypto.py` — szyfrowanie tokena KSeF i klucza symetrycznego (RSA-OAEP/SHA-256 kluczem MF), AES-256-CBC/PKCS7 dla faktur
- `src/ksef/testing.py` — narzędzia TYLKO na środowisko TEST: `random_nip()`, `generate_test_certificate(nip)` (samopodpisana pieczęć z `VATPL-{nip}` w OID 2.5.4.97), `build_test_invoice(seller_nip, buyer_nip, invoice_number)` (minimalna FA(3) VAT, zwalidowana XSD)
- `src/ksef/models.py` — dataclassy odpowiedzi (parsowanie camelCase → snake_case przez `from_json`)
- `src/ksef/exceptions.py` — `KsefError`, `KsefApiError` (HTTP ≥ 400), `KsefAuthenticationError`

## Fakty o KSeF API 2.0 (zweryfikowane 2026-07-07)

Źródło prawdy: https://github.com/CIRFMF/ksef-docs (m.in. `uwierzytelnianie.md`, `srodowiska.md`, `open-api.json`).

- Base URL: TEST `https://api-test.ksef.mf.gov.pl/v2`, DEMO `https://api-demo.ksef.mf.gov.pl/v2`, PRD `https://api.ksef.mf.gov.pl/v2`
- Przebieg uwierzytelnienia: `POST /auth/challenge` → podpisany XAdES `AuthTokenRequest` na `POST /auth/xades-signature` (lub `POST /auth/ksef-token` z tokenem zaszyfrowanym RSA-OAEP; klucz MF z `GET /security/public-key-certificates`, usage `KsefTokenEncryption`) → polling `GET /auth/{referenceNumber}` (Bearer = tymczasowy `authenticationToken`; `status.code` 200 = sukces, 1xx = w toku, ≥300 = błąd) → `POST /auth/token/redeem` → `accessToken` (krótki) + `refreshToken` (do 7 dni; odświeżanie `POST /auth/token/refresh`)
- XML: namespace `http://ksef.mf.gov.pl/auth/token/2.0`, XSD: `https://api-test.ksef.mf.gov.pl/docs/v2/schemas/authv2.xsd`; `ContextIdentifier` to XSD choice: `Nip`/`InternalId`/`NipVatUe`/`PeppolId`
- Środowisko TEST akceptuje certyfikaty samopodpisane i wymaga **losowych NIP-ów** (dane współdzielone między integratorami — nie używać prawdziwych). Domyślny `XAdESSigner` z signxml jest akceptowany przez KSeF (sprawdzone e2e)
- `POST /auth/token/redeem` z tym samym `authenticationToken` drugi raz → HTTP 400
- Odświeżony `accessToken` może być **identyczny** z poprzednim, jeśli refresh nastąpi w tej samej sekundzie (iat/exp w JWT mają ziarnistość sekundy) — nie porównywać tokenów w testach
- Tokeny KSeF: generowanie świadomie POZA biblioteką (w produkcji dostarczane z zewnątrz); testy e2e generują je pomocnikiem `_generate_ksef_token` w `tests/test_auth_e2e.py`. Endpointy: `POST /tokens` (Bearer, body `{"permissions": [...], "description": "..."}`, permissions np. `InvoiceRead`/`InvoiceWrite`) → `referenceNumber` + `token`; polling `GET /tokens/{referenceNumber}` aż `status` = `Active` (inne: `Pending`/`Revoking`/`Revoked`/`Failed`). Token zawiera znaki `|` — w shellu cytować
- Pobieranie faktur: `POST /invoices/query/metadata` (Bearer; body `subjectType` `Subject1`/`Subject2`/`Subject3`/`SubjectAuthorized` + `dateRange` `{dateType: Issue|Invoicing|PermanentStorage, from, to?}`, zakres maks. 3 miesiące; paginacja query `pageOffset`/`pageSize` 10–250; odpowiedź `invoices[]` + `hasMore`) oraz `GET /invoices/ksef/{ksefNumber}` → XML faktury (Accept: application/xml). Na TEST świeżo wysłana faktura jest widoczna w metadanych i do pobrania niemal od razu (sekundy); pobrany XML jest bajt w bajt tym, co wysłano
- Wysyłka (sesja interaktywna): `POST /sessions/online` (body: `formCode` `{systemCode: "FA (3)", schemaVersion: "1-0E", value: "FA"}` + `encryption` `{encryptedSymmetricKey, initializationVector, publicKeyId}`; klucz AES-256 + IV 16 B, klucz szyfrowany RSA-OAEP/SHA-256 certyfikatem MF usage `SymmetricKeyEncryption`) → `POST /sessions/online/{ref}/invoices` (body: `invoiceHash`+`invoiceSize` oryginału, `encryptedInvoiceHash`+`encryptedInvoiceSize`+`encryptedInvoiceContent` szyfrogramu AES-256-CBC/PKCS7, hashe SHA-256 Base64) → polling `GET /sessions/{ref}/invoices/{invRef}` (100/150 = w toku, 200 = sukces + `ksefNumber`, ≥400 = odrzucona) → `POST /sessions/online/{ref}/close` (204; UPO generowane asynchronicznie)
- FA(3): namespace `http://crd.gov.pl/wzor/2025/06/25/13775/`, XSD w ksef-docs `faktury/schemy/FA/schemat_FA(3)_v1-0E.xsd`. Zlokalizowany zestaw do walidacji offline leży w `schemas/fa3/fa3.xsd` (importowane schematy z crd.gov.pl pobrane obok, `schemaLocation` podmienione na lokalne): `etree.XMLSchema(etree.parse("schemas/fa3/fa3.xsd")).validate(doc)`. Minimalna faktura: `Naglowek` (KodFormularza@kodSystemowy="FA (3)"), `Podmiot1` (NIP+Nazwa+Adres), `Podmiot2` (jw. + **wymagane `JST` i `GV`**, "2" = nie), `Fa` (KodWaluty, P_1, P_2, P_13_1, P_14_1, P_15, Adnotacje z zagnieżdżonymi Zwolnienie/NoweSrodkiTransportu/PMarzy w wariancie „N", RodzajFaktury=VAT, FaWiersz)
- Formaty faktur na TEST: FA(2) i FA(3); DEMO/PRD: tylko FA(3)

## Stan prac (2026-07-08)

Cel projektu: pobieranie faktur z KSeF → zbudowanie pliku JPK do wysyłki.

Zrobione (wszystko zweryfikowane e2e na TEST, testy zielone):
- Uwierzytelnianie: oba warianty (certyfikat XAdES i token KSeF) + refresh
- Wysyłka FA(3): sesja interaktywna z szyfrowaniem AES, numer KSeF, zamknięcie sesji
- Pobieranie: metadane (z paginacją) i XML po numerze KSeF; pełna pętla wysyłka→pobranie w `test_send_invoice_and_download`

Do zrobienia (propozycje kolejnych kroków):
1. Budowa pliku JPK z pobranych faktur (parsowanie XML FA(3) → JPK_V7M do wysyłki)
2. UPO sesji/faktury, statusy, ew. wariant async klienta, CLI
