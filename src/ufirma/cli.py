"""Komenda ``ufirma`` — pełny obieg rozliczenia VAT z linii poleceń.

Entry point: ``ufirma = "ufirma.cli:app"``. Dwie grupy podkomend:
``ufirma ksef download`` (pobranie faktur z KSeF) oraz ``ufirma jpk
generate``/``send``/``status`` (budowa JPK_V7M, bramka e-Dokumenty, UPO).

Konfiguracja przez opcje albo zmienne środowiskowe — dla ``download``:
``KSEF_NIP``, ``KSEF_TOKEN``, ``KSEF_CERT``/``KSEF_KEY``, ``KSEF_ENV``;
dla ``generate``/``send``/``status``: ``JPK_NIP``, ``JPK_TAXPAYER_NAME`` lub
``JPK_TAXPAYER_FIRST_NAME``/``JPK_TAXPAYER_LAST_NAME``/
``JPK_TAXPAYER_BIRTH_DATE``, ``JPK_TAXPAYER_EMAIL``, ``JPK_TAX_OFFICE``,
``JPK_CERT``/``JPK_KEY`` albo ``JPK_PESEL``/``JPK_REVENUE`` oraz
``JPK_BRAMKA_ENV``. Przykład konfiguracji dla JDG: ``.env.example``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from cryptography import x509
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from jpk.bramka import AuthData, BramkaClient
from jpk.exceptions import JpkError
from jpk.fa3 import parse_invoice
from jpk.v7m import Taxpayer, build_jpk_v7m
from ksef import Environment, KsefClient, KsefError

app = typer.Typer(
    help="ufirma: KSeF → JPK — pobieranie faktur, budowa JPK_V7M"
    " i wysyłka do bramki e-Dokumenty.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
ksef_app = typer.Typer(help="Praca z KSeF: pobieranie faktur.", no_args_is_help=True)
jpk_app = typer.Typer(
    help="Budowa i wysyłka JPK_V7M do bramki e-Dokumenty.", no_args_is_help=True
)
app.add_typer(ksef_app, name="ksef")
app.add_typer(jpk_app, name="jpk")


@ksef_app.callback()
def _ksef() -> None:
    """Callback utrwala strukturę podkomend (``ufirma ksef download ...``) —

    bez niego Typer zwinąłby jedyną komendę grupy do jej korzenia.
    """


class EnvName(StrEnum):
    test = "test"
    demo = "demo"
    prod = "prod"


class SubjectType(StrEnum):
    Subject1 = "Subject1"
    Subject2 = "Subject2"
    Subject3 = "Subject3"
    SubjectAuthorized = "SubjectAuthorized"


class BramkaEnv(StrEnum):
    test = "test"
    prod = "prod"


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


@ksef_app.command()
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


@jpk_app.command()
def generate(
    period: Annotated[
        str,
        typer.Option(
            "--period", help="Okres rozliczeniowy RRRR-MM (data wystawienia)."
        ),
    ],
    nip: Annotated[str, typer.Option(envvar="JPK_NIP", help="NIP podatnika.")],
    email: Annotated[
        str, typer.Option(envvar="JPK_TAXPAYER_EMAIL", help="E-mail podatnika.")
    ],
    tax_office: Annotated[
        str,
        typer.Option(
            "--tax-office", envvar="JPK_TAX_OFFICE", help="Kod urzędu skarbowego."
        ),
    ],
    name: Annotated[
        str | None,
        typer.Option(
            envvar="JPK_TAXPAYER_NAME", help="Pełna nazwa podatnika (spółka)."
        ),
    ] = None,
    first_name: Annotated[
        str | None,
        typer.Option(
            "--first-name",
            envvar="JPK_TAXPAYER_FIRST_NAME",
            help="Imię podatnika (osoba fizyczna / JDG).",
        ),
    ] = None,
    last_name: Annotated[
        str | None,
        typer.Option(
            "--last-name",
            envvar="JPK_TAXPAYER_LAST_NAME",
            help="Nazwisko podatnika (osoba fizyczna / JDG).",
        ),
    ] = None,
    birth_date: Annotated[
        datetime | None,
        typer.Option(
            "--birth-date",
            envvar="JPK_TAXPAYER_BIRTH_DATE",
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
        raise _fail(f"Brak plików *.xml w {input_dir} — najpierw `ufirma ksef download`.")

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


@jpk_app.command()
def send(
    file: Annotated[Path, typer.Argument(help="Plik JPK (XML) do wysłania.")],
    cert_file: Annotated[
        Path | None,
        typer.Option(
            "--cert",
            envvar="JPK_CERT",
            help="Certyfikat PEM do podpisu XAdES (na produkcji kwalifikowany).",
        ),
    ] = None,
    key_file: Annotated[
        Path | None,
        typer.Option("--key", envvar="JPK_KEY", help="Klucz prywatny PEM do podpisu."),
    ] = None,
    revenue: Annotated[
        str | None,
        typer.Option(
            "--revenue",
            envvar="JPK_REVENUE",
            help="Dane autoryzujące zamiast podpisu (tylko osoba fizyczna):"
            " kwota przychodu za rok o dwa lata wcześniejszy (0 gdy brak).",
        ),
    ] = None,
    nip: Annotated[
        str | None,
        typer.Option(envvar="JPK_NIP", help="NIP podatnika (dane autoryzujące)."),
    ] = None,
    pesel: Annotated[
        str | None,
        typer.Option(
            envvar="JPK_PESEL", help="PESEL podatnika (dane autoryzujące, zamiast NIP)."
        ),
    ] = None,
    first_name: Annotated[
        str | None,
        typer.Option(
            "--first-name",
            envvar="JPK_TAXPAYER_FIRST_NAME",
            help="Imię podatnika (dane autoryzujące).",
        ),
    ] = None,
    last_name: Annotated[
        str | None,
        typer.Option(
            "--last-name",
            envvar="JPK_TAXPAYER_LAST_NAME",
            help="Nazwisko podatnika (dane autoryzujące).",
        ),
    ] = None,
    birth_date: Annotated[
        datetime | None,
        typer.Option(
            "--birth-date",
            envvar="JPK_TAXPAYER_BIRTH_DATE",
            formats=["%Y-%m-%d"],
            help="Data urodzenia RRRR-MM-DD (dane autoryzujące).",
        ),
    ] = None,
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
    """Wyślij plik JPK do bramki e-Dokumenty MF (domyślnie środowisko testowe).

    Uwierzytelnienie: podpis XAdES (--cert + --key) albo dane autoryzujące
    (--revenue + --nip/--pesel + --first-name + --last-name + --birth-date).
    """
    if not file.is_file():
        raise _fail(f"Nie znaleziono pliku {file}.")

    certificate = private_key = auth_data = None
    if cert_file is not None and key_file is not None and revenue is None:
        certificate = x509.load_pem_x509_certificate(cert_file.read_bytes())
        private_key = load_pem_private_key(key_file.read_bytes(), password=None)
    elif revenue is not None and cert_file is None and key_file is None:
        if not (first_name and last_name and birth_date):
            raise _fail(
                "Dane autoryzujące wymagają opcji --first-name, --last-name"
                " i --birth-date."
            )
        try:
            auth_data = AuthData(
                first_name=first_name,
                last_name=last_name,
                birth_date=birth_date.date(),
                revenue=revenue,
                nip=nip or None,
                pesel=pesel or None,
            )
        except JpkError as exc:
            raise _fail(str(exc)) from exc
    else:
        raise _fail(
            "Podaj dokładnie jedną metodę uwierzytelnienia: --cert z --key"
            " (podpis XAdES) albo --revenue z danymi osobowymi"
            " (dane autoryzujące, tylko osoba fizyczna)."
        )

    try:
        with BramkaClient(environment.value) as client:
            reference_number = client.send_jpk(
                file.read_bytes(),
                file_name=file.name,
                certificate=certificate,
                private_key=private_key,
                auth_data=auth_data,
            )
            typer.echo(f"Wysłano. Numer referencyjny: {reference_number}")
            if not wait:
                typer.echo(
                    f"Status sprawdzisz później: ufirma jpk status {reference_number} --env {environment.value}"
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
            f" ufirma jpk status {reference_number} --env {environment.value}"
        )
    if status.is_rejected:
        raise _fail("Dokument odrzucony przez bramkę.")


@jpk_app.command()
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
