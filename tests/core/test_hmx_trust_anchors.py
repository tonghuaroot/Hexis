from __future__ import annotations

from core.trust_anchors import (
    TrustAnchorVerifier,
    TrustStatus,
    TrustVerification,
    UnconfiguredTrustAnchors,
    require_verified,
)


def test_unconfigured_verifier_satisfies_interface_and_fails_closed():
    verifier = UnconfiguredTrustAnchors()
    assert isinstance(verifier, TrustAnchorVerifier)

    operator = verifier.verify_operator_signature(signature="claim", payload=b"body")
    source = verifier.verify_source_identity(source={"instance_id": "source"})
    lineage = verifier.verify_lineage(
        source={"hexis_lineage_id": "same"},
        local_instance_id="local",
        local_lineage_id="same",
    )

    assert {operator.status, source.status, lineage.status} == {TrustStatus.UNVERIFIED}
    assert all(not result.verified for result in (operator, source, lineage))
    assert lineage.metadata["claim_type"] == "lineage"


def test_unverified_signature_cannot_authorize_an_operation():
    result = UnconfiguredTrustAnchors().verify_operator_signature(
        signature="uncheckable", payload=b"replacement"
    )

    try:
        require_verified(result, operation="operator override")
    except PermissionError as exc:
        assert "no HMX trust anchors" in str(exc)
    else:
        raise AssertionError("unverified claim authorized an override")


def test_verified_and_invalid_results_preserve_audit_context():
    accepted = TrustVerification.accepted(
        anchor_id="operator-key-2026",
        metadata={"algorithm": "ed25519"},
    )
    rejected = TrustVerification.invalid(
        "signature mismatch", anchor_id="operator-key-2026"
    )

    require_verified(accepted, operation="operator override")
    assert accepted.verified
    assert accepted.metadata["algorithm"] == "ed25519"
    assert rejected.status is TrustStatus.INVALID
    assert not rejected.verified
