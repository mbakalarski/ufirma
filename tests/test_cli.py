from pathlib import Path

from lxml import etree
from typer.testing import CliRunner

from jpk import JPK_V7M_NAMESPACE
from jpk.v7m import ETD_NAMESPACE
from ksef.testing import FA3_NAMESPACE, build_test_invoice, random_nip
from jpk.cli import app as jpk_app
from ksef.cli import app

NS = {"jpk": JPK_V7M_NAMESPACE, "etd": ETD_NAMESPACE}
SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "jpk_v7m" / "jpk_v7m.xsd"

runner = CliRunner()


def write_invoice(
    directory: Path, seller_nip: str, invoice_number: str, issue_date: str
) -> str:
    """Zapisz fakturę testową z podmienioną datą wystawienia; zwróć numer KSeF."""
    root = etree.fromstring(
        build_test_invoice(seller_nip, random_nip(), invoice_number)
    )
    root.find(f"{{{FA3_NAMESPACE}}}Fa/{{{FA3_NAMESPACE}}}P_1").text = issue_date
    ksef_number = f"{seller_nip}-{issue_date.replace('-', '')}-ABCDEF123456-AB"
    (directory / f"{ksef_number}.xml").write_bytes(etree.tostring(root))
    return ksef_number


def test_generate_jpk(tmp_path: Path) -> None:
    seller_nip = random_nip()
    invoices_dir = tmp_path / "faktury"
    invoices_dir.mkdir()
    in_period = write_invoice(invoices_dir, seller_nip, "FV/1/2026", "2026-01-15")
    write_invoice(invoices_dir, seller_nip, "FV/2/2026", "2026-02-01")
    output = tmp_path / "out" / "JPK_V7M_2026-01.xml"

    result = runner.invoke(
        jpk_app,
        [
            "generate",
            "--period",
            "2026-01",
            "--nip",
            seller_nip,
            "--name",
            "Testowa Firma Sp. z o.o.",
            "--email",
            "firma@example.com",
            "--tax-office",
            "0202",
            "--input-dir",
            str(invoices_dir),
            "--output-dir",
            str(output.parent),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "faktur w ewidencji 1" in result.output
    assert "pominięto spoza okresu: 1" in result.output

    doc = etree.parse(str(output))
    schema = etree.XMLSchema(etree.parse(str(SCHEMA_PATH)))
    assert schema.validate(doc), schema.error_log
    rows = doc.findall("jpk:Ewidencja/jpk:SprzedazWiersz", namespaces=NS)
    assert len(rows) == 1
    assert rows[0].findtext("jpk:NrKSeF", namespaces=NS) == in_period
    naglowek = doc.find("jpk:Naglowek", namespaces=NS)
    assert naglowek.findtext("jpk:Rok", namespaces=NS) == "2026"
    assert naglowek.findtext("jpk:Miesiac", namespaces=NS) == "1"


def test_generate_jpk_natural_person(tmp_path: Path) -> None:
    seller_nip = random_nip()
    invoices_dir = tmp_path / "faktury"
    invoices_dir.mkdir()
    write_invoice(invoices_dir, seller_nip, "FV/1/2026", "2026-01-15")
    output = tmp_path / "out" / "JPK_V7M_2026-01.xml"

    result = runner.invoke(
        jpk_app,
        [
            "generate",
            "--period",
            "2026-01",
            "--nip",
            seller_nip,
            "--first-name",
            "Jan",
            "--last-name",
            "Kowalski",
            "--birth-date",
            "1980-05-01",
            "--email",
            "jan@example.com",
            "--tax-office",
            "0202",
            "--input-dir",
            str(invoices_dir),
            "--output-dir",
            str(output.parent),
        ],
    )
    assert result.exit_code == 0, result.output
    doc = etree.parse(str(output))
    osoba = doc.find("jpk:Podmiot1/jpk:OsobaFizyczna", namespaces=NS)
    assert osoba is not None
    assert osoba.findtext("etd:Nazwisko", namespaces=NS) == "Kowalski"


def test_generate_jpk_requires_taxpayer_identity(tmp_path: Path) -> None:
    invoices_dir = tmp_path / "faktury"
    invoices_dir.mkdir()
    write_invoice(invoices_dir, random_nip(), "FV/1/2026", "2026-01-15")
    result = runner.invoke(
        jpk_app,
        [
            "generate",
            "--period",
            "2026-01",
            "--nip",
            random_nip(),
            "--email",
            "f@example.com",
            "--tax-office",
            "0202",
            "--input-dir",
            str(invoices_dir),
        ],
        env={"JPK_TAXPAYER_NAME": ""},
    )
    assert result.exit_code == 1
    assert "osoby fizycznej" in result.output


def test_generate_jpk_rejects_bad_period(tmp_path: Path) -> None:
    result = runner.invoke(
        jpk_app,
        [
            "generate",
            "--period",
            "styczeń 2026",
            "--nip",
            random_nip(),
            "--name",
            "Firma",
            "--email",
            "f@example.com",
            "--tax-office",
            "0202",
            "--input-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert "RRRR-MM" in result.output


def test_generate_jpk_requires_invoices(tmp_path: Path) -> None:
    result = runner.invoke(
        jpk_app,
        [
            "generate",
            "--period",
            "2026-01",
            "--nip",
            random_nip(),
            "--name",
            "Firma",
            "--email",
            "f@example.com",
            "--tax-office",
            "0202",
            "--input-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert "download" in result.output


_NO_SEND_ENV = {
    "JPK_CERT": "",
    "JPK_KEY": "",
    "JPK_REVENUE": "",
    "JPK_NIP": "",
    "JPK_PESEL": "",
    "JPK_TAXPAYER_FIRST_NAME": "",
    "JPK_TAXPAYER_LAST_NAME": "",
    "JPK_TAXPAYER_BIRTH_DATE": "",
}


def test_send_requires_exactly_one_auth_method(tmp_path: Path) -> None:
    jpk_file = tmp_path / "JPK_V7M_2026-01.xml"
    jpk_file.write_bytes(b"<JPK/>")
    result = runner.invoke(jpk_app, ["send", str(jpk_file)], env=_NO_SEND_ENV)
    assert result.exit_code == 1
    assert "jedną metodę uwierzytelnienia" in result.output


def test_send_auth_data_requires_personal_data(tmp_path: Path) -> None:
    jpk_file = tmp_path / "JPK_V7M_2026-01.xml"
    jpk_file.write_bytes(b"<JPK/>")
    result = runner.invoke(
        jpk_app,
        ["send", str(jpk_file), "--revenue", "0", "--nip", random_nip()],
        env=_NO_SEND_ENV,
    )
    assert result.exit_code == 1
    assert "--birth-date" in result.output


def test_download_requires_credentials(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "download",
            "--from",
            "2026-01-01",
            "--to",
            "2026-01-31",
            "--nip",
            random_nip(),
            "--output-dir",
            str(tmp_path / "faktury"),
        ],
        env={"KSEF_TOKEN": "", "KSEF_CERT": "", "KSEF_KEY": ""},
    )
    assert result.exit_code == 1
    assert "poświadczeń" in result.output
