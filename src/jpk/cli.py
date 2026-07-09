"""Komenda ``jpk`` — budowa i wysyłka dokumentów JPK z linii poleceń.

Dane podatnika można podać opcjami albo zmiennymi środowiskowymi
(``KSEF_NIP``, ``KSEF_TAXPAYER_NAME`` lub ``KSEF_TAXPAYER_FIRST_NAME``/
``KSEF_TAXPAYER_LAST_NAME``/``KSEF_TAXPAYER_BIRTH_DATE``,
``KSEF_TAXPAYER_EMAIL``, ``KSEF_TAX_OFFICE``).
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from cryptography import x509
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from jpk.bramka import BramkaClient
from jpk.exceptions import JpkError
from jpk.fa3 import parse_invoice
from jpk.v7m import Taxpayer, build_jpk_v7m


class BramkaEnv(StrEnum):
    test = "test"
    prod = "prod"


app = typer.Typer(
    help="Budowa i wysyłka JPK_V7M.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


def _fail(message: str) -> typer.Exit:
    typer.secho(message, fg=typer.colors.RED, err=True)
    return typer.Exit(1)


@app.command()
def generate(
    period: Annotated[
        str,
        typer.Option(
            "--period", help="Okres rozliczeniowy RRRR-MM (data wystawienia)."
        ),
    ],
    nip: Annotated[str, typer.Option(envvar="KSEF_NIP", help="NIP podatnika.")],
    email: Annotated[
        str, typer.Option(envvar="KSEF_TAXPAYER_EMAIL", help="E-mail podatnika.")
    ],
    tax_office: Annotated[
        str,
        typer.Option(
            "--tax-office", envvar="KSEF_TAX_OFFICE", help="Kod urzędu skarbowego."
        ),
    ],
    name: Annotated[
        str | None,
        typer.Option(
            envvar="KSEF_TAXPAYER_NAME", help="Pełna nazwa podatnika (spółka)."
        ),
    ] = None,
    first_name: Annotated[
        str | None,
        typer.Option(
            "--first-name",
            envvar="KSEF_TAXPAYER_FIRST_NAME",
            help="Imię podatnika (osoba fizyczna / JDG).",
        ),
    ] = None,
    last_name: Annotated[
        str | None,
        typer.Option(
            "--last-name",
            envvar="KSEF_TAXPAYER_LAST_NAME",
            help="Nazwisko podatnika (osoba fizyczna / JDG).",
        ),
    ] = None,
    birth_date: Annotated[
        datetime | None,
        typer.Option(
            "--birth-date",
            envvar="KSEF_TAXPAYER_BIRTH_DATE",
            formats=["%Y-%m-%d"],
            help="Data urodzenia RRRR-MM-DD (osoba fizyczna / JDG).",
        ),
    ] = None,
    input_dir: Annotated[
        Path,
        typer.Option(
            "--input-dir", "-i", help="Katalog z XML faktur (nazwy = numery KSeF)."
        ),
    ] = Path("faktury"),
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir", "-o", help="Katalog zapisu JPK (plik JPK_V7M_<okres>.xml)."
        ),
    ] = Path("jpk"),
    purpose: Annotated[
        int, typer.Option(help="Cel złożenia: 1 = złożenie, 2 = korekta.")
    ] = 1,
) -> None:
    """Zbuduj JPK_V7M(3) z pobranych faktur sprzedaży za podany okres."""
    match = re.fullmatch(r"(\d{4})-(\d{2})", period)
    if match is None:
        raise _fail(f"Nieprawidłowy okres {period!r} — oczekiwany format RRRR-MM.")
    year, month = int(match.group(1)), int(match.group(2))

    try:
        taxpayer = Taxpayer(
            nip=nip,
            email=email,
            name=name,
            first_name=first_name,
            last_name=last_name,
            birth_date=birth_date.date() if birth_date else None,
        )
    except JpkError as exc:
        raise _fail(str(exc)) from exc

    files = sorted(input_dir.glob("*.xml"))
    if not files:
        raise _fail(f"Brak plików *.xml w {input_dir} — najpierw `ksef download`.")

    invoices = []
    skipped = 0
    for path in files:
        try:
            invoice = parse_invoice(path.read_bytes(), ksef_number=path.stem)
        except JpkError as exc:
            raise _fail(f"{path}: {exc}") from exc
        if (invoice.issue_date.year, invoice.issue_date.month) == (year, month):
            invoices.append(invoice)
        else:
            skipped += 1

    try:
        xml = build_jpk_v7m(
            invoices,
            taxpayer=taxpayer,
            year=year,
            month=month,
            tax_office_code=tax_office,
            purpose=purpose,
        )
    except JpkError as exc:
        raise _fail(str(exc)) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"JPK_V7M_{period}.xml"
    output_path.write_bytes(xml)
    typer.echo(
        f"JPK_V7M(3) za {period}: faktur w ewidencji {len(invoices)}"
        f" (pominięto spoza okresu: {skipped}) → {output_path}"
    )


@app.command()
def send(
    file: Annotated[Path, typer.Argument(help="Plik JPK (XML) do wysłania.")],
    cert_file: Annotated[
        Path,
        typer.Option(
            "--cert",
            envvar="KSEF_CERT",
            help="Certyfikat PEM do podpisu XAdES (na produkcji kwalifikowany).",
        ),
    ],
    key_file: Annotated[
        Path,
        typer.Option("--key", envvar="KSEF_KEY", help="Klucz prywatny PEM do podpisu."),
    ],
    environment: Annotated[
        BramkaEnv,
        typer.Option(
            "--env", envvar="JPK_BRAMKA_ENV", help="Środowisko bramki e-Dokumenty."
        ),
    ] = BramkaEnv.test,
    wait: Annotated[
        bool,
        typer.Option(
            "--wait/--no-wait",
            help="Czekaj na wynik przetwarzania (UPO lub odrzucenie).",
        ),
    ] = True,
    poll_timeout: Annotated[
        float, typer.Option(help="Maksymalny czas oczekiwania na wynik (sekundy).")
    ] = 600.0,
) -> None:
    """Wyślij plik JPK do bramki e-Dokumenty MF (domyślnie środowisko testowe)."""
    if not file.is_file():
        raise _fail(f"Nie znaleziono pliku {file}.")
    certificate = x509.load_pem_x509_certificate(cert_file.read_bytes())
    private_key = load_pem_private_key(key_file.read_bytes(), password=None)

    try:
        with BramkaClient(environment.value) as client:
            reference_number = client.send_jpk(
                file.read_bytes(),
                file_name=file.name,
                certificate=certificate,
                private_key=private_key,
            )
            typer.echo(f"Wysłano. Numer referencyjny: {reference_number}")
            if not wait:
                typer.echo(
                    f"Status sprawdzisz później: jpk status {reference_number} --env {environment.value}"
                )
                return
            status = client.wait_for_processing(
                reference_number, poll_timeout=poll_timeout
            )
    except JpkError as exc:
        raise _fail(str(exc)) from exc

    typer.echo(f"Status {status.code}: {status.description}")
    if status.is_accepted and status.upo:
        upo_path = file.with_suffix(".upo.xml")
        upo_path.write_text(status.upo, encoding="utf-8")
        typer.echo(f"UPO zapisane: {upo_path}")
    elif status.in_progress:
        typer.echo(
            "Przetwarzanie trwa — sprawdź później:"
            f" jpk status {reference_number} --env {environment.value}"
        )
    if status.is_rejected:
        raise _fail("Dokument odrzucony przez bramkę.")


@app.command()
def status(
    reference_number: Annotated[
        str, typer.Argument(help="Numer referencyjny sesji wysyłki.")
    ],
    environment: Annotated[
        BramkaEnv,
        typer.Option(
            "--env", envvar="JPK_BRAMKA_ENV", help="Środowisko bramki e-Dokumenty."
        ),
    ] = BramkaEnv.test,
    upo_output: Annotated[
        Path | None,
        typer.Option("--upo", help="Zapisz UPO do pliku (gdy dokument przyjęty)."),
    ] = None,
) -> None:
    """Sprawdź status przetwarzania wysłanego JPK (i pobierz UPO)."""
    try:
        with BramkaClient(environment.value) as client:
            submission = client.get_status(reference_number)
    except JpkError as exc:
        raise _fail(str(exc)) from exc
    typer.echo(f"Status {submission.code}: {submission.description}")
    if submission.details:
        typer.echo(f"Szczegóły: {submission.details}")
    if submission.upo and upo_output:
        upo_output.write_text(submission.upo, encoding="utf-8")
        typer.echo(f"UPO zapisane: {upo_output}")
    if submission.is_rejected:
        raise _fail("Dokument odrzucony przez bramkę.")
