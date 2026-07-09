"""Testy e2e uwierzytelniania na środowisku testowym KSeF (wymagają sieci)."""

import time

import pytest

from ksef import Environment, KsefClient
from ksef.testing import generate_test_certificate, random_nip

pytestmark = pytest.mark.e2e


def _generate_ksef_token(client: KsefClient) -> str:
    """Generuje token KSeF przez API i czeka na jego aktywację.

    Pomocnik testowy — generowanie tokenów świadomie nie wchodzi do biblioteki
    (tokeny mają być dostarczane z zewnątrz), stąd surowe wywołania API.
    """
    created = client._request(
        "POST",
        "/tokens",
        json={
            "permissions": ["InvoiceRead", "InvoiceWrite"],
            "description": "Token do testu e2e uwierzytelniania",
        },
    ).json()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status = client._request("GET", f"/tokens/{created['referenceNumber']}").json()[
            "status"
        ]
        if status == "Active":
            return created["token"]
        assert status == "Pending", f"token w nieoczekiwanym stanie {status}"
        time.sleep(1)
    pytest.fail("token KSeF nie osiągnął stanu Active w 60 s")


def test_fetch_challenge() -> None:
    with KsefClient(Environment.TEST) as client:
        challenge = client.fetch_challenge()
    assert len(challenge.challenge) == 36
    assert challenge.timestamp_ms > 0


def test_authenticate_with_self_signed_certificate() -> None:
    nip = random_nip()
    certificate, private_key = generate_test_certificate(nip)
    with KsefClient(Environment.TEST) as client:
        tokens = client.authenticate_with_certificate(nip, certificate, private_key)
        assert tokens.access_token.token
        assert tokens.refresh_token.token
        assert client.access_token is tokens.access_token

        # Nie porównujemy tokenów: JWT ma sekundową ziarnistość iat/exp,
        # więc refresh w tej samej sekundzie zwraca identyczny token.
        refreshed = client.refresh_access_token()
        assert refreshed.token
        assert client.access_token is refreshed


def test_authenticate_with_ksef_token() -> None:
    nip = random_nip()
    certificate, private_key = generate_test_certificate(nip)
    with KsefClient(Environment.TEST) as owner:
        owner.authenticate_with_certificate(nip, certificate, private_key)
        ksef_token = _generate_ksef_token(owner)

    with KsefClient(Environment.TEST) as client:
        tokens = client.authenticate_with_ksef_token(nip, ksef_token)
        assert tokens.access_token.token
        assert tokens.refresh_token.token
        assert client.access_token is tokens.access_token
