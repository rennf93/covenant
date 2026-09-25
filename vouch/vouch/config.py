"""Single source of truth for every environment variable the package reads.

One frozen pydantic-settings Settings tree, split into per-area submodels
(s1, s2, venue, broker, rules, ui, attest). Access via `load_settings()`
(cached: env is read once per process, which is also how the old
`os.environ.get` calls effectively behaved in every runner).

NAMING CONVENTION (the one comment on the trick): vouch's env vars are
arbitrary legacy names (VOUCH_S1_URL, COVENANT_ATTEST, ...), so a global
env_prefix cannot express them. Instead every field carries a
`validation_alias` that is the EXACT env var name the package has always
read, and every submodel is itself a pydantic-settings BaseSettings, so it
looks its own leaves up directly (a nested plain BaseModel would only
populate from JSON blobs or a delimiter scheme, which would rename every
var). .env.example, the Dockerfile and the dashboard's spawn-env builder
therefore keep working byte-for-byte. Add new vars by giving the new
submodel field the same alias style.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class _FrozenSettings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True)


class S1Settings(_FrozenSettings):
    """System-1 decision head (see engine/s1_backends.py)."""

    provider: str = Field(default="local", validation_alias="VOUCH_S1_PROVIDER")
    url: str = Field(default="", validation_alias="VOUCH_S1_URL")
    api_key: str = Field(default="", validation_alias="VOUCH_S1_API_KEY")
    model: str = Field(default="", validation_alias="VOUCH_S1_MODEL")
    base_url: str = Field(
        default="https://openrouter.ai/api/v1", validation_alias="VOUCH_S1_BASE_URL"
    )
    # Calibration file System-1 gates entries on (written by run_analysis.py
    # --fit). Legacy var name preserved: VOUCH_CALIBRATION.
    calibration: str = Field(default="out/calibration.json", validation_alias="VOUCH_CALIBRATION")


class S2Settings(_FrozenSettings):
    """System-2 rule rewriter endpoint (see engine/system2.py)."""

    base_url: str = Field(default="", validation_alias="VOUCH_S2_BASE_URL")
    model: str = Field(default="", validation_alias="VOUCH_S2_MODEL")
    key: str = Field(default="", validation_alias="VOUCH_S2_KEY")


class VenueSettings(_FrozenSettings):
    """Venue selection and per-venue configuration (see venues/)."""

    # "The flip switch": paper (default) | coinbase | arb-paper.
    name: str = Field(default="paper", validation_alias="VOUCH_VENUE")
    cb_key_name: str = Field(default="", validation_alias="VOUCH_CB_KEY_NAME")
    cb_private_key: str = Field(default="", validation_alias="VOUCH_CB_PRIVATE_KEY")
    cb_product: str = Field(default="SOL-USD", validation_alias="VOUCH_CB_PRODUCT")
    arb_fee_bps: float = Field(default=30.0, validation_alias="VOUCH_ARB_FEE_BPS")


class BrokerSettings(_FrozenSettings):
    """Paper broker cost model (see engine/broker.py)."""

    fee_bps: float = Field(default=60.0, validation_alias="VOUCH_FEE_BPS")


class RulesSettings(_FrozenSettings):
    """Raw JSON rules override injected by the dashboard (see engine/rules.py)."""

    raw: str = Field(default="", validation_alias="VOUCH_RULES")


class UiSettings(_FrozenSettings):
    """Dashboard gate. Compared with == "1" exactly like the old code, so
    VOUCH_UI_ALLOW_REAL=true does NOT enable real mode."""

    allow_real: str = Field(default="", validation_alias="VOUCH_UI_ALLOW_REAL")


class AttestSettings(_FrozenSettings):
    """Covenant attestation (see attest/commit.py)."""

    # Compared with == "1" exactly like the old code (ATTEST=1 enables; other
    # truthy strings do not).
    enabled: str = Field(default="", validation_alias="COVENANT_ATTEST")
    rpc_url: str = Field(default="", validation_alias="COVENANT_RPC_URL")
    private_key: str = Field(default="", validation_alias="COVENANT_PRIVATE_KEY")
    contract_address: str = Field(default="", validation_alias="COVENANT_CONTRACT_ADDRESS")
    chain: str = Field(default="arbitrum-sepolia", validation_alias="COVENANT_CHAIN")
    evidence_dir: str = Field(default="evidence", validation_alias="COVENANT_EVIDENCE_DIR")
    strategy_id: int = Field(default=0, validation_alias="COVENANT_STRATEGY_ID")
    strategy_name: str = Field(default="vouch-sol", validation_alias="COVENANT_STRATEGY_NAME")
    # Default metadata URI for run_register.py ("" = register without metadata).
    strategy_metadata_uri: str = Field(
        default="", validation_alias="COVENANT_STRATEGY_METADATA_URI"
    )
    # Optional kubo RPC base URL (e.g. http://127.0.0.1:5001). When set, the
    # evidence bundle is pinned there and the commit references ipfs://<cid>.
    ipfs_api: str = Field(default="", validation_alias="COVENANT_IPFS_API")


class Settings(_FrozenSettings):
    """Root settings tree. Frozen: mutate env, not the object (and call
    load_settings.cache_clear() if you must re-read within one process)."""

    log_level: str = Field(default="WARNING", validation_alias="VOUCH_LOG_LEVEL")

    s1: S1Settings = Field(default_factory=S1Settings)
    s2: S2Settings = Field(default_factory=S2Settings)
    venue: VenueSettings = Field(default_factory=VenueSettings)
    broker: BrokerSettings = Field(default_factory=BrokerSettings)
    rules: RulesSettings = Field(default_factory=RulesSettings)
    ui: UiSettings = Field(default_factory=UiSettings)
    attest: AttestSettings = Field(default_factory=AttestSettings)


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Read every VOUCH_*/COVENANT_* env var once per process."""
    return Settings()
