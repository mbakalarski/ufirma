# KSeF → JPK (Python)

Obsługa KSeF (Krajowy System e-Faktur) w Pythonie: klient KSeF API 2.0 (pakiet `ksef`), budowa i wysyłka dokumentów JPK (pakiet `jpk`). Każdy pakiet ma swoją komendę CLI w module `cli` (`ksef = "ksef.cli:app"`, `jpk = "jpk.cli:app"`). Układ `src/` z dwoma pakietami (`[tool.uv.build-backend] module-name = ["ksef", "jpk"]`), zarządzany przez **uv**. Komunikacja z użytkownikiem po polsku. **Nie używać poleceń git** — commitami zarządza użytkownik.

## Komendy

- `uv sync` — instalacja środowiska (Python 3.14, `.venv`)
- `uv run pytest` — wszystkie testy; testy z markerem `e2e` (pliki `tests/test_*_e2e.py`) uderzają w **żywe środowisko testowe KSeF** i wymagają sieci; są samowystarczalne (losowy NIP + samopodpisany certyfikat, bez env)
- `uv run pytest -m "not e2e"` — tylko testy offline
- `uv add <pkg>` / `uv add --dev <pkg>` — zależności (nie używać pip)

## Struktura

Pakiet `ksef` — klient KSeF API 2.0:

- `src/ksef/client.py` — `KsefClient` (httpx, sync) + `Environment` (TEST/DEMO/PROD); pełne przebiegi `authenticate_with_certificate()` i `authenticate_with_ksef_token()`; po uwierzytelnieniu nagłówek `Bearer` doklejany automatycznie; wysyłka: `open_online_session()` → `send_invoice()` → `wait_for_invoice()` (numer KSeF) → `close_online_session()`; pobieranie: `query_invoice_metadata()` / `iter_invoice_metadata()` (paginacja) i `get_invoice(ksef_number)` (XML)
- `src/ksef/auth.py` — budowa XML `AuthTokenRequest` (zgodnie z `authv2.xsd`) i podpis XAdES-BES (signxml)
- `src/ksef/crypto.py` — szyfrowanie tokena KSeF i klucza symetrycznego (RSA-OAEP/SHA-256 kluczem MF), AES-256-CBC/PKCS7 dla faktur
- `src/ksef/testing.py` — narzędzia TYLKO na środowisko TEST: `random_nip()`, `generate_test_certificate(nip)` (samopodpisana pieczęć z `VATPL-{nip}` w OID 2.5.4.97), `build_test_invoice(seller_nip, buyer_nip, invoice_number)` (minimalna FA(3) VAT, zwalidowana XSD; `buyer_nip=None` → nabywca BrakID, opcjonalne `currency`/`vat_pln` (P_14_1W)/`exchange_rate` (KursWaluty)/`buyer_country`)
- `src/ksef/models.py` — dataclassy odpowiedzi (parsowanie camelCase → snake_case przez `from_json`)
- `src/ksef/exceptions.py` — `KsefError`, `KsefApiError` (HTTP ≥ 400), `KsefAuthenticationError`

Pakiet `jpk` — budowa i wysyłka dokumentów JPK (na razie TYLKO faktury sprzedaży; możliwa przyszła rozbudowa o inne):

- `src/jpk/fa3.py` — `parse_invoice(xml, ksef_number, exchange_rate=None)` → `Fa3Invoice` (kwoty w słowniku `amounts` pod nazwami pól FA(3), pomocnik `amount()` zwraca 0 dla brakujących; `Buyer` z `tin`/`name`/`country_code`, `BRAK` gdy nabywca bez identyfikatora); numer KSeF trzeba podać z zewnątrz (z metadanych lub wyniku wysyłki) — XML faktury go nie zawiera; kurs waluty czytany z `FaWiersz/KursWaluty` (gdy jednolity we wszystkich wierszach), jawny `exchange_rate` ma pierwszeństwo
- `src/jpk/v7m.py` — `build_jpk_v7m(invoices, taxpayer=Taxpayer(...), year, month, tax_office_code, purpose=1)` → XML JPK_V7M(3); `Taxpayer(nip, email, name=...)` = spółka (OsobaNiefizyczna), `Taxpayer(nip, email, first_name=..., last_name=..., birth_date=...)` = osoba fizyczna/JDG (OsobaFizyczna); waliduje NIP sprzedawcy = NIP podatnika, RodzajFaktury VAT/KOR; waluty obce: podstawy × kurs (zaokrąglenie do grosza), podatek preferencyjnie z pól `P_14_xW`, brak kursu → `JpkError`; niezmapowane pola FA(3) (taxi 4%, OSS, odwrotne obciążenie, marża) i ujemny podatek należny → `JpkError`
- `src/jpk/bramka.py` — `BramkaClient(environment="test"|"prod")`: `send_jpk(jpk_xml, file_name=..., certificate=..., private_key=...)` → numer referencyjny, `get_status(ref)` / `wait_for_processing(ref)` → `SubmissionStatus` (`is_accepted`/`is_rejected`/`in_progress`, `upo`); `build_init_upload()` buduje metadane InitUpload
- `src/jpk/certs/` — certyfikaty klucza publicznego MF do szyfrowania klucza AES (`bramka-test.pem` ważny do 2027-07, `bramka-prod.pem`; rotowane przez MF — nowe na podatki.gov.pl „Pliki do pobrania")
- `src/jpk/cli.py` — komenda `jpk` (entry point `jpk = "jpk.cli:app"`): `jpk generate --period RRRR-MM` (czyta `*.xml` z `--input-dir`, domyślnie `faktury/`; numer KSeF z nazwy pliku; filtruje po dacie wystawienia P_1; zapis do `--output-dir`, domyślnie `jpk/`, plik `JPK_V7M_<okres>.xml`; dane podatnika: `--nip --email --tax-office` + `--name` (spółka) ALBO `--first-name --last-name --birth-date` (JDG); env `KSEF_NIP`/`KSEF_TAXPAYER_*`/`KSEF_TAX_OFFICE`), `jpk send <plik> --cert --key [--env test|prod] [--no-wait]` (po przyjęciu zapisuje UPO obok pliku jako `<nazwa>.upo.xml`), `jpk status <ref> [--upo plik]`
- `src/jpk/exceptions.py` — `JpkError`, `BramkaApiError` (HTTP z bramki)

Komenda `ksef` (moduł `src/ksef/cli.py`, Typer):

- `ksef download --from RRRR-MM-DD --to RRRR-MM-DD` — uwierzytelnienie tokenem KSeF (`--token`/`KSEF_TOKEN`) albo certyfikatem (`--cert`+`--key`, PEM), pobiera XML faktur (domyślnie `Subject1` = sprzedaż) do `--output-dir` (domyślnie `faktury/`), pliki nazywane numerami KSeF
- UWAGA: aplikacja ma jedną komendę — pusty `@app.callback()` jest konieczny, inaczej Typer zwija jedyną komendę do korzenia i `ksef download` przestaje działać
- błędy `KsefError`/`JpkError` → czerwony komunikat na stderr i kod wyjścia 1 (obie komendy CLI); testy offline w `tests/test_cli.py` (CliRunner), e2e pełnej pętli wysyłka→download→generate w `tests/test_cli_e2e.py`

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

## Fakty o JPK_V7M(3) (zweryfikowane 2026-07-09)

- Obowiązuje od 1 lutego 2026 (rozp. MFiG z 12.12.2025, publikacja w CRWDE 19.12.2025); nowości pod KSeF: w wierszu ewidencji wymagany XSD choice `NrKSeF`/`OFF`/`BFK`/`DI`
- XSD: `http://crd.gov.pl/wzor/2025/12/19/14090/schemat.xsd`, namespace = `http://crd.gov.pl/wzor/2025/12/19/14090/`. Zlokalizowana kopia do walidacji offline: `schemas/jpk_v7m/jpk_v7m.xsd` (importy `StrukturyDanych.xsd`, `KodyKrajow.xsd`, `KodyUrzedowSkarbowych.xsd` pobrane obok, bez dalszych zagnieżdżonych importów)
- Struktura: `JPK` = `Naglowek` + `Podmiot1` + `Deklaracja?` + `Ewidencja?`. Naglowek: `KodFormularza` "JPK_VAT" (@kodSystemowy="JPK_V7M (3)", @wersjaSchemy="1-0E"), `WariantFormularza`=3, `DataWytworzeniaJPK` (UTC z `Z`, min 2026-02-01T00:00:00Z), `CelZlozenia`@poz="P_7" (1=złożenie, 2=korekta), `KodUrzedu` (enum ~400 kodów czterocyfrowych), `Rok` ≥ 2026, `Miesiac`
- `Podmiot1`@rola="Podatnik": choice `OsobaFizyczna` (NIP, ImiePierwsze, Nazwisko, **wymagana DataUrodzenia**) | `OsobaNiefizyczna` (NIP, PelnaNazwa) + **wymagany `Email`**. UWAGA na namespace: pola OsobaFizyczna (NIP/ImiePierwsze/Nazwisko/DataUrodzenia) są w namespace **etd** (`.../2022/09/13/eD/DefinicjeTypy/`, typ z StrukturyDanych), a pola OsobaNiefizyczna i Email — w namespace JPK (tns)
- `Deklaracja` (opcjonalna): `KodFormularzaDekl` "VAT-7" (@kodSystemowy="VAT-7 (23)", @kodPodatku="VAT", @rodzajZobowiazania="Z", @wersjaSchemy="1-0E"), `WariantFormularzaDekl`=23; `PozycjeSzczegolowe` w **pełnych złotych** (`TKwotaC` integer; zaokrąglenie ≥50 gr w górę, art. 63 Ordynacji), wymagane tylko `P_38` (podatek należny razem) i `P_51` (do wpłaty, nieujemne), pary wymagane łącznie: (P_15,P_16), (P_17,P_18), (P_19,P_20); `Pouczenia`="1"
- `Ewidencja`: `SprzedazWiersz*` + `SprzedazCtrl` + `ZakupWiersz*` + `ZakupCtrl`; **oba Ctrl wymagane zawsze** (bez zakupów: LiczbaWierszyZakupow=0, PodatekNaliczony=0.00); kwoty K z 2 miejscami (`TKwotowy`); pary łączne: (K_15,K_16), (K_17,K_18), (K_19,K_20); kolejność w wierszu: Lp, [KodKrajuNadaniaTIN], NrKontrahenta, NazwaKontrahenta, DowodSprzedazy, DataWystawienia, [DataSprzedazy], NrKSeF, [TypDokumentu], [GTU/procedury], K_*
- Mapowanie FA(3) → kolumny sprzedaży: P_13_7→K_10 (zw), P_13_8+P_13_9→K_11 (poza terytorium), P_13_9→K_12, P_13_6_1→K_13 (0% kraj), P_13_3/P_14_3→K_15/K_16 (5%), P_13_2/P_14_2→K_17/K_18 (8%), P_13_1/P_14_1→K_19/K_20 (23%), P_13_6_2→K_21 (WDT), P_13_6_3→K_22 (eksport); deklaracja: P_10..P_22 = zaokrąglone sumy kolumn, P_37 = suma podstaw, P_38 = suma podatku
- `TNumerKSeF` pattern: `{NIP}-{RRRRMMDD}-{12 hex, opcjonalny myślnik po 6}-{2 hex}` — numery z API KSeF (`1796949259-20260708-010203040506-AB`) przechodzą
- Waluty obce: FA(3) NIE zawiera podstawy opodatkowania w PLN — tylko podatek w PLN w polach `P_14_xW`; podstawę do JPK trzeba przeliczyć kursem (art. 31a ustawy o VAT). Kurs bywa na fakturze w `FaWiersz/KursWaluty` (typ `TIlosci`, do 6 miejsc; `Fa/KursWalutyZ` dotyczy tylko zaliczek), ale jest opcjonalny — wtedy kurs musi podać wołający
- Nabywca bez identyfikatora (FA(3) `Podmiot2/DaneIdentyfikacyjne/BrakID=1`, np. konsument zagraniczny): w JPK `NrKontrahenta`="BRAK", bez `KodKrajuNadaniaTIN` (kod kraju z adresu nabywcy NIE jest kodem kraju nadania TIN)

## Fakty o bramce e-Dokumenty (wysyłka JPK; zweryfikowane e2e 2026-07-09)

Źródło: „Specyfikacja interfejsów usług JPK" 5.2.0 (https://www.podatki.gov.pl/media/c2sdatex/specyfikacja-interfejs%C3%B3w-us%C5%82ug-jpk-wersja-520.pdf); przykład InitUpload dla JPK_V7M(3): https://www.podatki.gov.pl/media/zlnfyez0/initupload-jpk_v7m-3.xml

- Adresy: TEST `https://test-e-dokumenty.mf.gov.pl`, PROD `https://e-dokumenty.mf.gov.pl`; metody `POST /api/Storage/InitUploadSigned` (application/xml), `PUT` do Azure Blob (URL+nagłówki z odpowiedzi InitUploadSigned, w tym `Content-MD5` i `x-ms-blob-type: BlockBlob`), `POST /api/Storage/FinishUpload` (JSON: `ReferenceNumber` + `AzureBlobNameList`), `GET /api/Storage/Status/{ref}`
- Przygotowanie pliku: ZIP (DEFLATE, jeden plik w archiwum) → podział binarny na części ≤60 MB → każda część szyfrowana AES-256-CBC/PKCS#7 (jeden klucz i jeden IV dla wszystkich części); klucz szyfrowany **RSA/ECB/PKCS#1 v1.5** (inaczej niż KSeF, który używa OAEP!) certyfikatem MF (inny dla TEST i PROD, rotowane ~co 2 lata, kopie w `src/jpk/certs/`)
- `InitUpload`: namespace `http://e-dokumenty.mf.gov.pl`, `DocumentType`=JPK, `Version`=`01.02.01.20160617`, FormCode dla JPK_V7M(3): `<FormCode systemCode="JPK_V7M (3)" schemaVersion="1-0E">JPK_VAT</FormCode>`; hash dokumentu SHA-256 Base64, hash części **MD5** Base64; nazwa pliku wg `[a-zA-Z0-9_.-]{5,55}`
- Podpis: XAdES-BES enveloped lub enveloping, RSA-SHA256; deklaracja XML musi mieć postać dokładnie `<?xml version="1.0" encoding="utf-8"?>` (lxml domyślnie daje apostrofy — dokleić deklarację ręcznie). Na TEST podpis samopodpisany jest akceptowany (weryfikacja kwalifikowanego tylko z `?enableValidateQualifiedSignature=true`); na PROD wymagany kwalifikowany/zaufany
- **Certyfikat podpisu musi mieć atrybut `serialNumber` (OID 2.5.4.5) w formacie ETSI, np. `TINPL-{nip}`** — bez niego status 423 „Dokument z certyfikatem bez wymaganych atrybutów"; goły NIP w serialNumber też odpada; samo `organizationIdentifier` (VATPL-, wystarczające dla KSeF) NIE wystarcza bramce. Jeden certyfikat z oboma atrybutami działa w KSeF i bramce (tak robi `generate_test_certificate`)
- Statusy (`GET Status`): 1xx stan sesji (100 rozpoczęta, 120 zakończona, trwa weryfikacja), 3xx faza przetwarzania, 200 = przyjęty + pole `Upo` (XML), 4xx = odrzucony (401 niezgodny z XSD, 407 duplikat, 413 zły hash, 423 atrybuty certyfikatu…), wyjątkowo 300 = zły numer referencyjny. Na TEST przetwarzanie trwa sekundy
- Duplikaty wykrywane po SHA-256 dokumentu (InitUploadSigned → HTTP 400 kod 170 albo status 407)

## Stan prac (2026-07-09)

Cel projektu: pobieranie faktur z KSeF → zbudowanie pliku JPK do wysyłki.

Zrobione (wszystko zweryfikowane e2e na TEST, testy zielone):
- Uwierzytelnianie: oba warianty (certyfikat XAdES i token KSeF) + refresh
- Wysyłka FA(3): sesja interaktywna z szyfrowaniem AES, numer KSeF, zamknięcie sesji
- Pobieranie: metadane (z paginacją) i XML po numerze KSeF; pełna pętla wysyłka→pobranie w `test_send_invoice_and_download`
- Pakiet `jpk`: parser FA(3) + budowa JPK_V7M(3) dla faktur sprzedaży (ewidencja z numerami KSeF + deklaracja VAT-7(23)), w tym waluty obce (podstawa × kurs, VAT z `P_14_xW`) i nabywcy bez identyfikatora (BrakID→"BRAK"); wynik walidowany offline schematem MF w testach (`tests/test_jpk.py`)
- Wysyłka JPK do bramki e-Dokumenty (`jpk/bramka.py`): pełny przebieg ZIP→AES→XAdES→upload→UPO zweryfikowany e2e na `test-e-dokumenty.mf.gov.pl` — status 200 i UPO (`tests/test_bramka_e2e.py`)
- Dwie komendy CLI: `ksef download` + `jpk generate`/`jpk send`/`jpk status`; pętla wysyłka→download→generate zweryfikowana e2e (`tests/test_cli_e2e.py`)

Do zrobienia (propozycje kolejnych kroków):
1. Pakiet `jpk` — rozszerzenia: inne rodzaje faktur (ZAL/ROZ/UPR), GTU/procedury, faktury zakupowe
2. UPO sesji/faktury w KSeF, ew. wariant async klienta
