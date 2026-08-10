from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class AuthChallenge:
    challenge: str
    timestamp: datetime
    timestamp_ms: int

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> AuthChallenge:
        return cls(
            challenge=data["challenge"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            timestamp_ms=data["timestampMs"],
        )


@dataclass(frozen=True)
class TokenInfo:
    token: str
    valid_until: datetime

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TokenInfo:
        return cls(
            token=data["token"],
            valid_until=datetime.fromisoformat(data["validUntil"]),
        )


@dataclass(frozen=True)
class AuthenticationInit:
    """Response to an authentication init (XAdES signature or KSeF token)."""

    reference_number: str
    authentication_token: TokenInfo

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> AuthenticationInit:
        return cls(
            reference_number=data["referenceNumber"],
            authentication_token=TokenInfo.from_json(data["authenticationToken"]),
        )


@dataclass(frozen=True)
class AuthStatus:
    code: int
    description: str
    details: list[str] = field(default_factory=list)
    is_token_redeemed: bool | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> AuthStatus:
        status = data["status"]
        return cls(
            code=status["code"],
            description=status.get("description", ""),
            details=status.get("details") or [],
            is_token_redeemed=data.get("isTokenRedeemed"),
        )


@dataclass(frozen=True)
class AuthTokens:
    access_token: TokenInfo
    refresh_token: TokenInfo

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> AuthTokens:
        return cls(
            access_token=TokenInfo.from_json(data["accessToken"]),
            refresh_token=TokenInfo.from_json(data["refreshToken"]),
        )


@dataclass(frozen=True)
class OnlineSession:
    """An open interactive session together with the invoice encryption key."""

    reference_number: str
    valid_until: datetime
    key: bytes
    iv: bytes


@dataclass(frozen=True)
class SessionInvoice:
    """Status of an invoice sent in a session (``GET /sessions/{ref}/invoices/{invRef}``)."""

    reference_number: str
    ordinal_number: int
    status_code: int
    status_description: str
    status_details: list[str] = field(default_factory=list)
    ksef_number: str | None = None
    invoice_number: str | None = None
    acquisition_date: datetime | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SessionInvoice:
        status = data["status"]
        acquisition = data.get("acquisitionDate")
        return cls(
            reference_number=data["referenceNumber"],
            ordinal_number=data["ordinalNumber"],
            status_code=status["code"],
            status_description=status.get("description", ""),
            status_details=status.get("details") or [],
            ksef_number=data.get("ksefNumber"),
            invoice_number=data.get("invoiceNumber"),
            acquisition_date=datetime.fromisoformat(acquisition)
            if acquisition
            else None,
        )


@dataclass(frozen=True)
class InvoiceSeller:
    nip: str
    name: str | None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> InvoiceSeller:
        return cls(nip=data["nip"], name=data.get("name"))


@dataclass(frozen=True)
class InvoiceBuyer:
    """Invoice buyer; ``identifier_type``: Nip/VatUe/Other/None."""

    identifier_type: str
    identifier_value: str | None
    name: str | None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> InvoiceBuyer:
        identifier = data["identifier"]
        return cls(
            identifier_type=identifier["type"],
            identifier_value=identifier.get("value"),
            name=data.get("name"),
        )


@dataclass(frozen=True)
class FormCode:
    """Invoice form code, e.g. systemCode ``FA (3)``, value ``FA``."""

    system_code: str
    schema_version: str
    value: str

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FormCode:
        return cls(
            system_code=data["systemCode"],
            schema_version=data["schemaVersion"],
            value=data["value"],
        )


@dataclass(frozen=True)
class InvoiceMetadata:
    """Invoice metadata from ``POST /invoices/query/metadata``."""

    ksef_number: str
    invoice_number: str
    issue_date: date
    invoicing_date: datetime
    acquisition_date: datetime
    permanent_storage_date: datetime
    seller: InvoiceSeller
    buyer: InvoiceBuyer
    net_amount: float
    gross_amount: float
    vat_amount: float
    currency: str
    invoicing_mode: str
    invoice_type: str
    form_code: FormCode
    is_self_invoicing: bool
    has_attachment: bool
    invoice_hash: str

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> InvoiceMetadata:
        return cls(
            ksef_number=data["ksefNumber"],
            invoice_number=data["invoiceNumber"],
            issue_date=date.fromisoformat(data["issueDate"][:10]),
            invoicing_date=datetime.fromisoformat(data["invoicingDate"]),
            acquisition_date=datetime.fromisoformat(data["acquisitionDate"]),
            permanent_storage_date=datetime.fromisoformat(data["permanentStorageDate"]),
            seller=InvoiceSeller.from_json(data["seller"]),
            buyer=InvoiceBuyer.from_json(data["buyer"]),
            net_amount=data["netAmount"],
            gross_amount=data["grossAmount"],
            vat_amount=data["vatAmount"],
            currency=data["currency"],
            invoicing_mode=data["invoicingMode"],
            invoice_type=data["invoiceType"],
            form_code=FormCode.from_json(data["formCode"]),
            is_self_invoicing=data["isSelfInvoicing"],
            has_attachment=data["hasAttachment"],
            invoice_hash=data["invoiceHash"],
        )


@dataclass(frozen=True)
class InvoiceMetadataPage:
    """A single result page of an invoice metadata query."""

    invoices: list[InvoiceMetadata]
    has_more: bool
    is_truncated: bool

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> InvoiceMetadataPage:
        return cls(
            invoices=[InvoiceMetadata.from_json(item) for item in data["invoices"]],
            has_more=data["hasMore"],
            is_truncated=data["isTruncated"],
        )


@dataclass(frozen=True)
class EncryptionCertificate:
    """MF public key certificate used for encryption (e.g. of KSeF tokens)."""

    certificate_der: bytes
    certificate_id: str
    public_key_id: str
    usage: list[str]
    valid_from: datetime
    valid_to: datetime

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> EncryptionCertificate:
        import base64

        return cls(
            certificate_der=base64.b64decode(data["certificate"]),
            certificate_id=data["certificateId"],
            public_key_id=data["publicKeyId"],
            usage=list(data.get("usage") or []),
            valid_from=datetime.fromisoformat(data["validFrom"]),
            valid_to=datetime.fromisoformat(data["validTo"]),
        )
