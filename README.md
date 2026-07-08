# ksef

Klient KSeF API 2.0 (Krajowy System e-Faktur) w Pythonie.

## Stan

Wczesny etap. Działa uwierzytelnianie (KSeF API 2.0):

- podpisem XAdES (certyfikat kwalifikowany / na środowisku testowym samopodpisany),
- tokenem KSeF (RSA-OAEP),
- odświeżanie `accessToken`.

## Szybki start (środowisko testowe)

```python
from ksef import Environment, KsefClient
from ksef.testing import generate_test_certificate, random_nip

nip = random_nip()
certificate, private_key = generate_test_certificate(nip)

with KsefClient(Environment.TEST) as client:
    tokens = client.authenticate_with_certificate(nip, certificate, private_key)
    print(tokens.access_token.token)
```

## Rozwój

```bash
uv sync                       # środowisko
uv run pytest                 # wszystkie testy (e2e wymagają sieci)
uv run pytest -m "not e2e"    # tylko offline
```
