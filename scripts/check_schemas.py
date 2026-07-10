"""Sprawdź, czy schematy w ``schemas/`` są zgodne z publikowanymi przez MF.

Lokalne kopie służą do walidacji offline i mają podmienione ``schemaLocation``
na ścieżki lokalne, więc porównanie ignoruje ten atrybut. Dodatkowo pilnowany
jest ``authv2.xsd`` KSeF (nie wendorowany — klient buduje XML według niego),
przez skrót SHA-256. crd.gov.pl odrzuca domyślnego User-Agenta bibliotek HTTP
i przekierowuje na https — stąd nagłówek przeglądarkowy.

Uruchomienie (stdlib, bez zależności): ``python scripts/check_schemas.py``.
Kod wyjścia 1, gdy cokolwiek różni się od upstreamu — wtedy trzeba pobrać
nowe wersje, podmienić schemaLocation na lokalne i przejrzeć zmiany.
"""

import hashlib
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# plik lokalny -> adres aktualnej wersji publikowanej przez MF
VENDORED = {
    "schemas/fa3/fa3.xsd": "http://crd.gov.pl/wzor/2025/06/25/13775/schemat.xsd",
    "schemas/fa3/StrukturyDanych.xsd": "http://crd.gov.pl/xml/schematy/dziedzinowe/mf/2022/01/05/eD/DefinicjeTypy/StrukturyDanych_v10-0E.xsd",
    "schemas/fa3/ElementarneTypyDanych.xsd": "http://crd.gov.pl/xml/schematy/dziedzinowe/mf/2022/01/05/eD/DefinicjeTypy/ElementarneTypyDanych_v10-0E.xsd",
    "schemas/fa3/KodyKrajow.xsd": "http://crd.gov.pl/xml/schematy/dziedzinowe/mf/2022/01/05/eD/DefinicjeTypy/KodyKrajow_v10-0E.xsd",
    "schemas/jpk_v7m/jpk_v7m.xsd": "http://crd.gov.pl/wzor/2025/12/19/14090/schemat.xsd",
    "schemas/jpk_v7m/StrukturyDanych.xsd": "http://crd.gov.pl/xml/schematy/dziedzinowe/mf/2022/09/13/eD/DefinicjeTypy/StrukturyDanych_v12-0E.xsd",
    "schemas/jpk_v7m/KodyKrajow.xsd": "http://crd.gov.pl/xml/schematy/dziedzinowe/mf/2023/09/06/eD/KodyKrajow/KodyKrajow_v13-0E.xsd",
    "schemas/jpk_v7m/KodyUrzedowSkarbowych.xsd": "http://crd.gov.pl/xml/schematy/dziedzinowe/mf/2022/01/05/eD/KodyUrzedowSkarbowych/KodyUrzedowSkarbowych_v8-0E.xsd",
    "schemas/sig/sig-2008_v2-0.xsd": "https://www.podatki.gov.pl/media/10553/sig-2008_v2-0.xsd",
}

# adres -> oczekiwany SHA-256 (schematy używane, ale nie wendorowane)
PINNED = {
    "https://api-test.ksef.mf.gov.pl/docs/v2/schemas/authv2.xsd": "617579d059d25ac0acab338736d6f1c25e807278d8ca6dc8fc23454089209b75",
}


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def normalized(xsd: bytes) -> bytes:
    return re.sub(rb'schemaLocation="[^"]*"', b"", xsd)


def main() -> int:
    stale = 0
    for local, url in VENDORED.items():
        upstream = fetch(url)
        if normalized((ROOT / local).read_bytes()) == normalized(upstream):
            print(f"ZGODNY  {local}")
        else:
            stale += 1
            print(f"RÓŻNY   {local}\n        upstream: {url}")
    for url, expected in PINNED.items():
        digest = hashlib.sha256(fetch(url)).hexdigest()
        if digest == expected:
            print(f"ZGODNY  {url}")
        else:
            stale += 1
            print(f"RÓŻNY   {url}\n        sha256 teraz: {digest}, oczekiwany: {expected}")
    if stale:
        print(f"\nSchematy nieaktualne: {stale} — pobierz nowe wersje i zaktualizuj kod.")
        return 1
    print("\nWszystkie schematy aktualne.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
