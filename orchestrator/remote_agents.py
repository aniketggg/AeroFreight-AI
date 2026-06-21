"""Remote uAgents clients for orchestrator teammate integration."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from economic_agent.messages import EconomistError, EconomistRequest, EconomistResponse
from shared_models import EconData, ShipmentRequest


class RemoteEconomistError(RuntimeError):
    """Raised when the remote Economist agent cannot be reached or reply is invalid."""


class UAgentsEconomistClient:
    """Call Ashwin's Economist uAgent via Context.send_and_receive."""

    def __init__(
        self,
        context: Any,
        destination: str,
        timeout_seconds: int = 30,
    ) -> None:
        self.context = context
        self.destination = destination
        self.timeout_seconds = timeout_seconds

    async def analyze(self, shipment: ShipmentRequest) -> EconData:
        """Send ShipmentRequest JSON and return parsed EconData from the remote agent."""
        request = EconomistRequest(shipment_json=shipment.model_dump_json())

        try:
            reply, status = await self.context.send_and_receive(
                self.destination,
                request,
                response_type={EconomistResponse, EconomistError},
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            logger = getattr(self.context, "logger", None)
            if logger is not None:
                logger.error(
                    "Remote Economist communication failed: %s",
                    type(exc).__name__,
                )
            raise RemoteEconomistError(
                "The remote Economist agent could not be reached."
            ) from exc

        if reply is None:
            logger = getattr(self.context, "logger", None)
            if logger is not None and status is not None:
                detail = getattr(status, "detail", None)
                if detail and isinstance(detail, str):
                    logger.error("Remote Economist returned no reply")
            raise RemoteEconomistError(
                "The remote Economist agent did not respond."
            )

        if isinstance(reply, EconomistError):
            raise RemoteEconomistError(
                "The Economist agent could not process the shipment request."
            )

        if not isinstance(reply, EconomistResponse):
            raise RemoteEconomistError(
                "The remote Economist agent returned an unexpected response."
            )

        try:
            return EconData.model_validate_json(reply.econ_data_json)
        except ValidationError as exc:
            raise RemoteEconomistError(
                "The remote Economist agent returned invalid data."
            ) from exc
