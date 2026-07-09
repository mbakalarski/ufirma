"""Komenda ``ksef`` — praca z KSeF z linii poleceń (pobieranie faktur).

Entry point: ``ksef = "ksef.cli:app"``.

Poświadczenia można podać opcjami albo zmiennymi środowiskowymi
(``KSEF_NIP``, ``KSEF_TOKEN``, ``KSEF_CERT``/``KSEF_KEY``, ``KSEF_ENV``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from cryptography import x509
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from ksef import Environment, KsefClient, KsefError

app = typer.Typer(
    help="Narzędzia KSeF: pobieranie faktur.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


class EnvName(StrEnum):
    test = "test"
    demo = "demo"
    prod = "prod"


class SubjectType(StrEnum):
    Subject1 = "Subject1"
    Subject2 = "Subject2"
    Subject3 = "Subject3"
    SubjectAuthorized = "SubjectAuthorized"


@app.callback()
def _main() -> None:
    """Callback utrwala strukturę podkomend (``ksef download ...``) —

    bez niego Typer zwinąłby jedyną komendę do korzenia aplikacji.
    """


def _fail(message: str) -> typer.Exit:
    typer.secho(message, fg=typer.colors.RED, err=True)
    return typer.Exit(1)


def _authenticate(
    client: KsefClient,
    nip: str,
    token: str | None,
    cert_file: Path | None,
    key_file: Path | None,
) -> None:
    if token:
        client.authenticate_with_ksef_token(nip, token)
        return
    certificate = x509.load_pem_x509_certificate(cert_file.read_bytes())  # type: ignore[union-attr]
    private_key = load_pem_private_key(key_file.read_bytes(), password=None)  # type: ignore[union-attr]
    client.authenticate_with_certificate(nip, certificate, private_key)


@app.command()
def download(
    from_date: Annotated[
        datetime,
        typer.Option("--from", help="Początek zakresu dat wystawienia (RRRR-MM-DD)."),
    ],
    to_date: Annotated[
        datetime,
        typer.Option(
            "--to", help="Koniec zakresu dat wystawienia (RRRR-MM-DD, włącznie)."
        ),
    ],
    nip: Annotated[
        str, typer.Option(envvar="KSEF_NIP", help="NIP kontekstu (podatnika).")
    ],
    token: Annotated[
        str | None,
        typer.Option(envvar="KSEF_TOKEN", help="Token KSeF (uwierzytelnienie)."),
    ] = None,
    cert_file: Annotated[
        Path | None,
        typer.Option(
            "--cert", envvar="KSEF_CERT", help="Certyfikat PEM (zamiast tokena)."
        ),
    ] = None,
    key_file: Annotated[
        Path | None,
        typer.Option(
            "--key", envvar="KSEF_KEY", help="Klucz prywatny PEM (zamiast tokena)."
        ),
    ] = None,
    environment: Annotated[
        EnvName, typer.Option("--env", envvar="KSEF_ENV", help="Środowisko KSeF.")
    ] = EnvName.test,
    subject_type: Annotated[
        SubjectType,
        typer.Option(help="Rola w fakturze (Subject1 = sprzedaż podatnika)."),
    ] = SubjectType.Subject1,
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir", "-o", help="Katalog na pobrane XML (nazwy = numery KSeF)."
        ),
    ] = Path("faktury"),
) -> None:
    """Pobierz faktury z KSeF (XML) za zakres dat wystawienia."""
    if token and (cert_file or key_file):
        raise _fail(
            "Podaj albo token KSeF, albo parę --cert/--key — nie jedno i drugie."
        )
    if not token and not (cert_file and key_file):
        raise _fail(
            "Brak poświadczeń: podaj token KSeF (--token/KSEF_TOKEN) albo --cert i --key."
        )
    date_from = from_date.replace(tzinfo=UTC)
    date_to = to_date.replace(hour=23, minute=59, second=59, tzinfo=UTC)

    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    try:
        with KsefClient(Environment[environment.value.upper()]) as client:
            _authenticate(client, nip, token, cert_file, key_file)
            for metadata in client.iter_invoice_metadata(
                subject_type.value, date_from, date_to
            ):
                xml = client.get_invoice(metadata.ksef_number)
                (output_dir / f"{metadata.ksef_number}.xml").write_bytes(xml)
                typer.echo(f"{metadata.ksef_number}  {metadata.invoice_number}")
                count += 1
    except KsefError as exc:
        raise _fail(f"Błąd KSeF: {exc}") from exc
    typer.echo(f"Pobrano faktur: {count} → {output_dir}")
