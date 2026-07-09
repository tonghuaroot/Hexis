"""Fail-closed trust-anchor interface for HMX identity claims.

HMX deliberately does not choose a global PKI. Deployments plug in their own
verifier, while the unconfigured default makes the absence of verification
explicit and never turns a claimed signature or lineage into authorization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable


class TrustStatus(str, Enum):
    """The security-relevant outcome of checking one trust claim."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    INVALID = "invalid"


@dataclass(frozen=True)
class TrustVerification:
    """A verifier result suitable for audit records and CLI diagnostics."""

    status: TrustStatus
    reason: str
    anchor_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        return self.status is TrustStatus.VERIFIED

    @classmethod
    def accepted(
        cls,
        *,
        anchor_id: str,
        reason: str = "claim verified against configured trust anchor",
        metadata: Mapping[str, Any] | None = None,
    ) -> "TrustVerification":
        return cls(TrustStatus.VERIFIED, reason, anchor_id, metadata or {})

    @classmethod
    def unverified(
        cls,
        reason: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> "TrustVerification":
        return cls(TrustStatus.UNVERIFIED, reason, metadata=metadata or {})

    @classmethod
    def invalid(
        cls,
        reason: str,
        *,
        anchor_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "TrustVerification":
        return cls(TrustStatus.INVALID, reason, anchor_id, metadata or {})


@runtime_checkable
class TrustAnchorVerifier(Protocol):
    """Deployment-provided verification boundary used by HMX import paths.

    ``payload`` is the exact canonical bytes covered by a signature. Lineage
    verification receives both ends of the claim because equality of two
    unverified labels is not proof of shared lineage.
    """

    def verify_operator_signature(
        self,
        *,
        signature: str,
        payload: bytes,
        operator_identity: str | None = None,
    ) -> TrustVerification: ...

    def verify_source_identity(
        self,
        *,
        source: Mapping[str, Any],
        signature: str | None = None,
        payload: bytes | None = None,
    ) -> TrustVerification: ...

    def verify_lineage(
        self,
        *,
        source: Mapping[str, Any],
        local_instance_id: str,
        local_lineage_id: str,
    ) -> TrustVerification: ...


class UnconfiguredTrustAnchors:
    """Explicit default for deployments with no configured trust anchors.

    This is intentionally not a permissive verifier. Callers may display the
    claimed values as labels, but every authorization decision must observe
    ``verified is False``.
    """

    _REASON = "no HMX trust anchors are configured; the claim is a label, not proof"

    def verify_operator_signature(
        self,
        *,
        signature: str,
        payload: bytes,
        operator_identity: str | None = None,
    ) -> TrustVerification:
        return TrustVerification.unverified(
            self._REASON,
            metadata={"claim_type": "operator_signature"},
        )

    def verify_source_identity(
        self,
        *,
        source: Mapping[str, Any],
        signature: str | None = None,
        payload: bytes | None = None,
    ) -> TrustVerification:
        return TrustVerification.unverified(
            self._REASON,
            metadata={"claim_type": "source_identity"},
        )

    def verify_lineage(
        self,
        *,
        source: Mapping[str, Any],
        local_instance_id: str,
        local_lineage_id: str,
    ) -> TrustVerification:
        return TrustVerification.unverified(
            self._REASON,
            metadata={"claim_type": "lineage"},
        )


def require_verified(result: TrustVerification, *, operation: str) -> None:
    """Fail closed at an authorization boundary with an actionable reason."""

    if not result.verified:
        raise PermissionError(
            f"{operation} requires a verified trust claim: {result.reason}"
        )
