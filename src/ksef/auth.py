"""Budowanie i podpisywanie dokumentu AuthTokenRequest (KSeF API 2.0)."""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes
from cryptography.x509 import Certificate
from lxml import etree

AUTH_NAMESPACE = "http://ksef.mf.gov.pl/auth/token/2.0"

# Dozwolone typy identyfikatora kontekstu wg authv2.xsd
CONTEXT_NIP = "Nip"
CONTEXT_INTERNAL_ID = "InternalId"
CONTEXT_NIP_VAT_UE = "NipVatUe"
CONTEXT_PEPPOL_ID = "PeppolId"


def build_auth_token_request(
    challenge: str,
    context_type: str,
    context_value: str,
    subject_identifier_type: str = "certificateSubject",
) -> etree._Element:
    """Zbuduj niepodpisany dokument AuthTokenRequest zgodny z authv2.xsd."""
    ns = f"{{{AUTH_NAMESPACE}}}"
    root = etree.Element(f"{ns}AuthTokenRequest", nsmap={None: AUTH_NAMESPACE})
    etree.SubElement(root, f"{ns}Challenge").text = challenge
    context = etree.SubElement(root, f"{ns}ContextIdentifier")
    etree.SubElement(context, f"{ns}{context_type}").text = context_value
    etree.SubElement(root, f"{ns}SubjectIdentifierType").text = subject_identifier_type
    return root


def sign_xades(
    element: etree._Element,
    certificate: Certificate,
    private_key: PrivateKeyTypes,
) -> bytes:
    """Podpisz dokument podpisem XAdES-BES (enveloped) i zwróć XML jako bajty."""
    from signxml.xades import XAdESSigner

    cert_pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    signed = XAdESSigner().sign(element, key=key_pem, cert=cert_pem)
    return etree.tostring(signed, xml_declaration=True, encoding="UTF-8")
