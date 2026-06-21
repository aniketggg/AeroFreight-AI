"""Offline inspection of installed Fetch/uAgents payment protocol types."""

from __future__ import annotations

import importlib.metadata
import json
import sys

from treasury_agent.payment_protocol import build_payment_protocol

from orchestrator.payment_trace import (
    PLACEHOLDER_CHECKOUT,
    build_orchestrator_request_payment,
    build_treasury_request_payment,
    compare_request_payment_dumps,
    summarize_request_payment_dump,
)


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def _print_section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    from uagents_core.contrib.protocols.payment import (
        Funds,
        RequestPayment,
        payment_protocol_spec,
    )

    _print_section("Package versions")
    for package in ("uagents", "uagents-core", "stripe"):
        print(f"{package}: {_package_version(package)}")

    _print_section("RequestPayment annotations")
    print(json.dumps({k: str(v) for k, v in RequestPayment.__annotations__.items()}, indent=2))

    _print_section("RequestPayment JSON schema")
    print(json.dumps(RequestPayment.model_json_schema(), indent=2))

    _print_section("RequestPayment.metadata annotation")
    print(str(RequestPayment.__annotations__.get("metadata")))

    _print_section("Funds annotations")
    print(json.dumps({k: str(v) for k, v in Funds.__annotations__.items()}, indent=2))
    print(json.dumps(Funds.model_json_schema(), indent=2))

    _print_section("payment_protocol_spec")
    print(f"name={payment_protocol_spec.name} version={payment_protocol_spec.version}")

    async def _noop(*_args, **_kwargs) -> None:
        return None

    payment_proto = build_payment_protocol(_noop, _noop)
    _print_section("Protocol message digests")
    print(f"manifest_digest={payment_proto.digest}")
    print(
        "incoming_models="
        + json.dumps(
            sorted(
                getattr(model, "__name__", str(model))
                for model in payment_proto.models.values()
            )
        )
    )

    checkout = dict(PLACEHOLDER_CHECKOUT)
    orchestrator_request = build_orchestrator_request_payment(
        recipient="agent1qorchestrator",
        session_id="session-diagnostic",
        fee_usd=5.0,
        checkout=checkout,
    )
    treasury_request = build_treasury_request_payment(
        recipient="agent1qtreasury",
        session_id="session-diagnostic",
        fee_usd=5.0,
        checkout=checkout,
    )
    orchestrator_dump = orchestrator_request.model_dump()
    treasury_dump = treasury_request.model_dump()

    _print_section("Sanitized orchestrator RequestPayment model_dump()")
    print(json.dumps(summarize_request_payment_dump(orchestrator_dump), indent=2))

    stripe_meta = orchestrator_dump.get("metadata", {}).get("stripe")
    _print_section("Serialization survival checks")
    print(f"metadata.stripe is dict: {isinstance(stripe_meta, dict)}")
    print(f"ui_mode survives: {stripe_meta.get('ui_mode') if isinstance(stripe_meta, dict) else None}")
    print(
        "id aliases survive: "
        f"{isinstance(stripe_meta, dict) and stripe_meta.get('id') == stripe_meta.get('checkout_session_id')}"
    )
    print(
        "amount_cents type: "
        f"{type(stripe_meta.get('amount_cents')).__name__ if isinstance(stripe_meta, dict) else None}"
    )
    print(
        "Funds.amount type: "
        f"{type(orchestrator_dump['accepted_funds'][0]['amount']).__name__}"
    )
    print(
        "nested dict permitted in metadata: "
        f"{isinstance(orchestrator_dump.get('metadata', {}).get('stripe'), dict)}"
    )

    comparison = compare_request_payment_dumps(orchestrator_dump, treasury_dump)
    _print_section("Standalone Treasury vs orchestrator structural comparison")
    print(json.dumps(comparison, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
