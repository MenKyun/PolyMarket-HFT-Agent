from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass(slots=True)
class MarketConfig:
    yes_token_id: str
    no_token_id: str
    market_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    ws_ping_interval_sec: float = 10.0
    book_stale_ms: int = 1_500
    custom_feature_enabled: bool = True


@dataclass(slots=True)
class SourceConfig:
    name: str
    url: str
    probability_json_path: str
    weight: float = 1.0
    api_key_env: Optional[str] = None
    api_key_header: str = "Authorization"
    api_key_prefix: str = "Bearer "
    poll_interval_ms: int = 500
    request_timeout_ms: int = 1_500
    headers: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True


@dataclass(slots=True)
class StrategyConfig:
    edge_threshold_bps: float = 30.0
    exit_threshold_bps: float = 20.0
    min_sources: int = 2
    max_source_disagreement_bps: float = 60.0
    rearm_ms: int = 750
    min_top_level_liquidity: float = 25.0
    max_midpoint_spread_bps: float = 500.0
    trend_window: int = 64
    fast_ema_alpha: float = 0.25
    slow_ema_alpha: float = 0.05
    trend_threshold_bps: float = 20.0


@dataclass(slots=True)
class RiskConfig:
    max_capital_per_trade_pct: float = 0.005
    daily_stop_pct: float = 0.02
    chop_regime_multiplier: float = 0.5
    trend_regime_multiplier: float = 1.5
    flat_regime_multiplier: float = 1.0
    max_asset_position_shares: float = 2_500.0
    fee_bps: float = 0.0
    reserve_cash_pct: float = 0.10
    allow_short_inventory: bool = False


@dataclass(slots=True)
class AccountConfig:
    starting_cash_usd: float = 100_000.0


@dataclass(slots=True)
class RuntimeConfig:
    paper_mode: bool = True
    allow_live_orders: bool = False
    log_level: str = "INFO"
    logs_dir: str = "./logs"
    health_interval_sec: int = 5
    queue_maxsize: int = 100_000
    decision_loop_sleep_ms: int = 5


@dataclass(slots=True)
class AppConfig:
    market: MarketConfig
    sources: List[SourceConfig]
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    account: AccountConfig = field(default_factory=AccountConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)

    @staticmethod
    def from_dict(raw: Dict[str, Any]) -> "AppConfig":
        market = MarketConfig(**raw["market"])
        sources = [SourceConfig(**item) for item in raw.get("sources", [])]
        strategy = StrategyConfig(**raw.get("strategy", {}))
        risk = RiskConfig(**raw.get("risk", {}))
        account = AccountConfig(**raw.get("account", {}))
        runtime = RuntimeConfig(**raw.get("runtime", {}))
        return AppConfig(
            market=market,
            sources=sources,
            strategy=strategy,
            risk=risk,
            account=account,
            runtime=runtime,
        )


def load_config(path: str | Path) -> AppConfig:
    with Path(path).expanduser().open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError("Config root must be a YAML mapping")
    return AppConfig.from_dict(raw)
