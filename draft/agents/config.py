"""Shared configuration for the AeroFreight swarm.

Agent addresses are derived deterministically from their seeds, so every agent
(and the orchestrator in particular) knows the others' addresses up-front with
zero import coupling. Verified: ``Identity.from_seed(seed, 0).address`` equals
``Agent(seed=seed).address``.
"""

from uagents.crypto import Identity

# --- Deterministic seeds (stable addresses across runs) ---
ORCH_SEED = "aerofreight-orchestrator-seed-v1"
TARIFF_SEED = "aerofreight-tariff-seed-v1"
FREIGHT_SEED = "aerofreight-freight-seed-v1"
ESCROW_SEED = "aerofreight-escrow-seed-v1"
TESTER_SEED = "aerofreight-tester-client-seed-v1"


def address_for(seed: str) -> str:
    """Derive an agent's bech32 address from its seed without instantiating it."""
    return Identity.from_seed(seed, 0).address


# Pre-computed addresses (import these anywhere; no agent objects required).
ORCH_ADDRESS = address_for(ORCH_SEED)
TARIFF_ADDRESS = address_for(TARIFF_SEED)
FREIGHT_ADDRESS = address_for(FREIGHT_SEED)
ESCROW_ADDRESS = address_for(ESCROW_SEED)
TESTER_ADDRESS = address_for(TESTER_SEED)

# --- Mock data API + static web (one FastAPI process serves both) ---
API_HOST = "127.0.0.1"
API_PORT = 8080
API_BASE_URL = f"http://{API_HOST}:{API_PORT}"
# The static Bill-of-Lading / success page is mounted under /app by the API.
WEB_BASE_URL = f"{API_BASE_URL}/app"

# --- Business defaults ---
# Sarah's prompt never states the goods' declared value (needed for the duty
# calc). The demo implies ~$2,800 -> $70 duty at 2.5%. Parser uses this default.
DEFAULT_DECLARED_VALUE_USD = 2800.0

# send_and_receive timeout for orchestrator -> sub-agent calls (seconds).
SUBAGENT_TIMEOUT = 30
