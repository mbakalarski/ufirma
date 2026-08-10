"""e2e authentication tests against the KSeF TEST environment (need network)."""

import time

import pytest

from ksef import Environment, KsefClient
from ksef.testing import generate_test_certificate, random_nip

pytestmark = pytest.mark.e2e


def _generate_ksef_token(client: KsefClient) -> str:
    """Generate a KSeF token through the API and wait until it becomes active.

    Test helper only: token generation is deliberately kept out of the library
    (tokens are meant to be provided from outside), hence the raw API calls.
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
    pytest.fail("KSeF token did not reach the Active state within 60 s")


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

        # Do not compare the tokens: JWT iat/exp have one-second granularity,
        # so refreshing within the same second returns an identical token.
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
