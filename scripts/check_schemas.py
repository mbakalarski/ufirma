"""Check the MF schemas this project depends on. Two independent sections.

``--vendored`` — do our copies in ``src/jpk/schemas/`` still equal what MF
published? Every copy is compared byte for byte with the file behind its
upstream URL, ignoring ``schemaLocation`` (the local copies have it rewritten
to sibling paths). This is an integrity check of the vendoring, not a
freshness check: CRWDE forms are immutable and the shared type schemas carry
their version in the file name, so those URLs cannot start serving something
else. It catches a botched re-vendoring — a truncated file, the wrong version
dropped into a directory, a schema "fixed" locally to make a test pass — which
matters because we validate other people's JPK with these files. Run it when
the copies change, i.e. on pull requests touching them.

``--upstream`` — has MF moved on? A new form never appears under an existing
URL, so this section watches the index pages where a successor shows up: the
KAS list of JPK structures, the KSeF schema directory in CIRFMF/ksef-docs and
the MF list of XML structures (for the gateway signature schema). It also
covers the two sources that genuinely can change in place: KSeF's
``authv2.xsd``, served from a fixed documentation URL and pinned by SHA-256,
and the SIG schema, a CMS media file that can be re-uploaded under the same
id. Run it periodically.

Without flags both sections run. Stdlib only, no dependencies. Exit code 1
when a copy differs, when a newer form shows up, or when an index page stops
yielding the links we parse — a silent "all good" from a redesigned page would
be worse than a false alarm. crd.gov.pl rejects the default User-Agent of HTTP
libraries and redirects to https, hence the browser-like header.

After MF publishes a successor the whole update is one edit here: point the
``VENDORED`` URLs at the new form, re-download the files, add the new id to the
known set below — and release, because users validate with the copy from their
installed package.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# local file -> URL of the version currently published by MF
VENDORED = {
    "src/jpk/schemas/fa3/fa3.xsd": "http://crd.gov.pl/wzor/2025/06/25/13775/schemat.xsd",
    "src/jpk/schemas/fa3/StrukturyDanych.xsd": "http://crd.gov.pl/xml/schematy/dziedzinowe/mf/2022/01/05/eD/DefinicjeTypy/StrukturyDanych_v10-0E.xsd",
    "src/jpk/schemas/fa3/ElementarneTypyDanych.xsd": "http://crd.gov.pl/xml/schematy/dziedzinowe/mf/2022/01/05/eD/DefinicjeTypy/ElementarneTypyDanych_v10-0E.xsd",
    "src/jpk/schemas/fa3/KodyKrajow.xsd": "http://crd.gov.pl/xml/schematy/dziedzinowe/mf/2022/01/05/eD/DefinicjeTypy/KodyKrajow_v10-0E.xsd",
    "src/jpk/schemas/jpk_v7m/jpk_v7m.xsd": "http://crd.gov.pl/wzor/2025/12/19/14090/schemat.xsd",
    "src/jpk/schemas/jpk_v7m/StrukturyDanych.xsd": "http://crd.gov.pl/xml/schematy/dziedzinowe/mf/2022/09/13/eD/DefinicjeTypy/StrukturyDanych_v12-0E.xsd",
    "src/jpk/schemas/jpk_v7m/KodyKrajow.xsd": "http://crd.gov.pl/xml/schematy/dziedzinowe/mf/2023/09/06/eD/KodyKrajow/KodyKrajow_v13-0E.xsd",
    "src/jpk/schemas/jpk_v7m/KodyUrzedowSkarbowych.xsd": "http://crd.gov.pl/xml/schematy/dziedzinowe/mf/2022/01/05/eD/KodyUrzedowSkarbowych/KodyUrzedowSkarbowych_v8-0E.xsd",
    "src/jpk/schemas/sig/sig-2008_v2-0.xsd": "https://www.podatki.gov.pl/media/10553/sig-2008_v2-0.xsd",
}

# Vendored copies whose upstream can be replaced in place, so they are worth
# re-checking periodically and not only when we touch them. CRWDE forms are
# signed and immutable; a file in the podatki.gov.pl CMS is not.
MUTABLE_UPSTREAM = ("src/jpk/schemas/sig/sig-2008_v2-0.xsd",)

# URL -> expected SHA-256 (used but not vendored: the client builds XML
# according to it, and the documentation endpoint is not versioned)
PINNED = {
    "https://api-test.ksef.mf.gov.pl/docs/v2/schemas/authv2.xsd": "617579d059d25ac0acab338736d6f1c25e807278d8ca6dc8fc23454089209b75",
}

# --- Index pages that would reveal a successor to a form we vendor ---

# KAS list of JPK structures; the CRWDE links there are the JPK_V7M/V7K family
# across all its versions. 14090 is our JPK_V7M(3), 14089 its V7K sibling.
KAS_JPK_STRUCTURES = "https://www.gov.pl/web/kas/struktury-jpk"
KNOWN_JPK_FORM_IDS = {"9393", "9394", "11148", "11149", "14089", "14090"}

# KSeF invoice schemas live in the MF documentation repository; a new FA form
# lands here as another file. Set GITHUB_TOKEN to avoid the anonymous rate limit.
KSEF_FA_SCHEMAS = (
    "https://api.github.com/repos/CIRFMF/ksef-docs/contents/faktury/schemy/FA"
)
KNOWN_FA_SCHEMAS = {"schemat_FA(2)_v1-0E.xsd", "schemat_FA(3)_v1-0E.xsd"}

# MF list of XML structures; used only to watch the signature schema of the
# e-Dokumenty gateway (authorizing data), which is not a CRWDE form.
MF_XML_STRUCTURES = (
    "https://www.podatki.gov.pl/e-deklaracje/dokumentacja-it/struktury-dokumentow-xml/"
)
KNOWN_SIG_SCHEMAS = {"sig-2008_v2-0.xsd"}


def fetch(url: str, headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", **(headers or {})}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def normalized(xsd: bytes) -> bytes:
    return re.sub(rb'schemaLocation="[^"]*"', b"", xsd)


def check_vendored(only: tuple[str, ...] | None = None) -> int:
    """Compare vendored copies with upstream; returns the number of problems."""
    problems = 0
    for local in only or VENDORED:
        url = VENDORED[local]
        if normalized((ROOT / local).read_bytes()) == normalized(fetch(url)):
            print(f"CURRENT {local}")
        else:
            problems += 1
            print(f"CHANGED {local}\n        upstream: {url}")
    return problems


def check_pinned() -> int:
    """Verify schemas we use but do not vendor, by digest."""
    problems = 0
    for url, expected in PINNED.items():
        digest = hashlib.sha256(fetch(url)).hexdigest()
        if digest == expected:
            print(f"CURRENT {url}")
        else:
            problems += 1
            print(f"CHANGED {url}\n        sha256 now: {digest}, expected: {expected}")
    return problems


def _report_new(what: str, found: set[str], known: set[str], source: str) -> int:
    """Compare what an index page offers with what we know; 1 = needs a look."""
    if not found:
        print(f"BROKEN  {what}\n        no known links found at {source}")
        return 1
    unknown = sorted(found - known)
    if not unknown:
        print(f"NEWEST  {what}")
        return 0
    print(f"NEWER   {what}: {', '.join(unknown)}\n        source: {source}")
    return 1


def check_jpk_forms() -> int:
    """Has a JPK structure newer than the vendored JPK_V7M(3) been published?"""
    page = fetch(KAS_JPK_STRUCTURES).decode("utf-8", "replace")
    found = set(re.findall(r"crd\.gov\.pl/wzor/\d{4}/\d{2}/\d{2}/(\d+)/", page))
    return _report_new(
        "JPK structures (CRWDE forms)", found, KNOWN_JPK_FORM_IDS, KAS_JPK_STRUCTURES
    )


def check_fa_schemas() -> int:
    """Has a KSeF invoice schema newer than FA(3) been published?"""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    entries = json.loads(fetch(KSEF_FA_SCHEMAS, headers))
    found = {e["name"] for e in entries if e.get("type") == "file"}
    return _report_new("KSeF invoice schemas", found, KNOWN_FA_SCHEMAS, KSEF_FA_SCHEMAS)


def check_sig_schema() -> int:
    """Has a signature schema newer than SIG-2008 v2-0 been published?"""
    page = fetch(MF_XML_STRUCTURES).decode("utf-8", "replace")
    found = set(re.findall(r"(sig-2008_v[\d-]+\.xsd)", page.lower()))
    return _report_new(
        "gateway signature schema", found, KNOWN_SIG_SCHEMAS, MF_XML_STRUCTURES
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the MF schemas vendored and used by this project.",
        epilog="Without flags both sections run.",
    )
    parser.add_argument(
        "--vendored",
        action="store_true",
        help="integrity: do our copies still equal what MF published",
    )
    parser.add_argument(
        "--upstream",
        action="store_true",
        help="freshness: has MF published a newer form or changed a live schema",
    )
    args = parser.parse_args(argv)
    both = not (args.vendored or args.upstream)

    problems = 0
    if args.vendored or both:
        problems += check_vendored()
    if args.upstream or both:
        if not both:
            # In the periodic run the copies are not re-checked wholesale, only
            # the one whose upstream can be swapped in place.
            problems += check_vendored(MUTABLE_UPSTREAM)
        problems += check_pinned()
        print()
        problems += check_jpk_forms() + check_fa_schemas() + check_sig_schema()

    if problems:
        print(
            f"\nNeeds attention: {problems}. Re-vendor the schemas, update the"
            " known sets and release — users validate with the copy from their"
            " installed package."
        )
        return 1
    if both:
        print("\nCopies match upstream; no newer forms published.")
    elif args.vendored:
        print("\nCopies match what MF published.")
    else:
        print("\nNo newer forms published; live schemas unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
