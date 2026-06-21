"""Real on-chain escrow settlement on the Fetch.ai testnet (dorado-1).

When a shipment's escrow is authorized, a persistent **platform wallet** — funded
from the public dorado faucet — signs a real token transfer into a deterministic
**escrow vault** address via cosmpy, producing a real, explorer-verifiable
transaction hash. This is genuine on-chain settlement on Fetch.ai's test network.

Testnet only: the transferred FET amount is a symbolic lock (the USD terms live in
the Bill of Lading and the tx memo), so no real-world money moves.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request

from cosmpy.aerial.client import LedgerClient, NetworkConfig
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.crypto.address import Address
from cosmpy.crypto.keypairs import PrivateKey

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_WALLET_FILE = os.path.join(_DATA_DIR, "platform_wallet.key")

NETWORK = NetworkConfig.fetchai_stable_testnet()  # dorado-1
DENOM = "afet"
FAUCET_URL = "https://faucet-dorado.fetch.ai/api/v3/claims"
# Block explorer for the dorado testnet (transaction permalink).
EXPLORER_TX = "https://explore-dorado.fetch.ai/transactions/{hash}"

# Deterministic seed -> stable escrow vault address that receives the locked funds.
_ESCROW_VAULT_SEED = "aerofreight-escrow-vault-v1"
# Symbolic lock amount per escrow (0.001 TESTFET). Override via env if desired.
ESCROW_AMOUNT_AFET = int(os.getenv("AEROFREIGHT_ESCROW_AFET", str(10 ** 15)))
# Keep the platform wallet topped up so it can pay amount + gas.
_MIN_BALANCE_AFET = 5 * 10 ** 16
_FAUCET_WAIT_S = 75

_client: LedgerClient | None = None


def client() -> LedgerClient:
    global _client
    if _client is None:
        _client = LedgerClient(NETWORK)
    return _client


def _is_transient(exc: Exception) -> bool:
    s = str(exc)
    return any(
        marker in s
        for marker in ("UNAVAILABLE", "No route to host", "Stream removed", "Deadline", "ConnectError")
    )


def _with_retry(fn, attempts: int = 4, delay: float = 2.0):
    """Retry a network call through transient gRPC hiccups, reconnecting between tries."""
    global _client
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not _is_transient(exc):
                raise
            _client = None  # force a fresh channel on the next attempt
            if i < attempts - 1:
                time.sleep(delay)
    raise last  # type: ignore[misc]


def _vault_wallet() -> LocalWallet:
    # 32-byte private key deterministically derived from a fixed seed -> stable address.
    key = hashlib.sha256(_ESCROW_VAULT_SEED.encode()).digest()
    return LocalWallet(PrivateKey(key))


def vault_address() -> str:
    return str(_vault_wallet().address())


def platform_wallet() -> LocalWallet:
    """The funded signer. Persisted to data/platform_wallet.key (gitignored)."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    env_key = os.getenv("AEROFREIGHT_WALLET_KEY")
    if env_key:
        return LocalWallet(PrivateKey(bytes.fromhex(env_key.strip())))
    if os.path.exists(_WALLET_FILE):
        with open(_WALLET_FILE) as fh:
            return LocalWallet(PrivateKey(bytes.fromhex(fh.read().strip())))
    pk = PrivateKey()
    with open(_WALLET_FILE, "w") as fh:
        fh.write(pk.private_key_hex)
    try:
        os.chmod(_WALLET_FILE, 0o600)
    except OSError:
        pass
    return LocalWallet(pk)


def platform_address() -> str:
    return str(platform_wallet().address())


def balance(address: str | None = None) -> int:
    address = address or platform_address()
    return _with_retry(lambda: client().query_bank_balance(address, DENOM))


def request_faucet(address: str) -> dict:
    body = json.dumps({"address": address}).encode()
    req = urllib.request.Request(
        FAUCET_URL, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def ensure_funded(min_afet: int = _MIN_BALANCE_AFET, wait_s: int = _FAUCET_WAIT_S) -> int:
    """Top up the platform wallet from the faucet if it's below ``min_afet``.

    Fails FAST (raises) when the wallet has no funds and the faucet rejects the
    claim (e.g. per-IP cooldown), so the /authorize request doesn't hang. Only
    polls for delivery when the faucet actually accepted the claim.
    """
    address = platform_address()
    bal = balance(address)
    if bal >= min_afet:
        return bal
    try:
        request_faucet(address)
    except Exception as exc:  # noqa: BLE001 — faucet rejected (likely rate-limited)
        if bal <= 0:
            raise RuntimeError(
                f"platform wallet {address} is unfunded and the testnet faucet is "
                f"unavailable ({exc}). Fund it once via the dorado faucet to enable settlement."
            ) from exc
        return bal  # have some funds already; proceed with what we have
    # Faucet accepted the claim — poll briefly for delivery.
    deadline = time.time() + wait_s
    while time.time() < deadline:
        bal = balance(address)
        if bal >= min_afet:
            return bal
        time.sleep(3)
    return bal


def submit_escrow(
    contract_id: str, memo: str | None = None, amount_afet: int = ESCROW_AMOUNT_AFET
) -> dict:
    """Sign + broadcast a real on-chain escrow lock; return the tx details.

    Raises on failure (caller surfaces the error to the page).
    """
    wallet = platform_wallet()
    # Ensure enough for the lock amount plus generous gas headroom.
    funded = ensure_funded(min_afet=amount_afet + 2 * 10 ** 16)
    if funded < amount_afet:
        raise RuntimeError(
            f"platform wallet underfunded ({funded} afet) — faucet may be rate-limited"
        )
    memo = memo or f"AeroFreight escrow {contract_id}"
    tx = client().send_tokens(Address(vault_address()), amount_afet, DENOM, wallet, memo=memo)
    tx.wait_to_complete()
    return {
        "tx_hash": tx.tx_hash,
        "explorer_url": EXPLORER_TX.format(hash=tx.tx_hash),
        "from_address": platform_address(),
        "vault_address": vault_address(),
        "amount_afet": amount_afet,
        "amount_fet": amount_afet / 10 ** 18,
        "denom": DENOM,
        "chain_id": NETWORK.chain_id,
        "network": "fetchai-dorado-testnet",
        "memo": memo,
    }


if __name__ == "__main__":
    print("platform:", platform_address())
    print("vault:   ", vault_address())
    print("balance: ", balance(), "afet")
