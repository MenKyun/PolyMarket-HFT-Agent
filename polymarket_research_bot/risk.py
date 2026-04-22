from __future__ import annotations

from .config import RiskConfig
from .execution import PaperExecutor
from .models import BookState, ConsensusPrediction, TradeIntent


class RiskManager:
    def __init__(self, cfg: RiskConfig, executor: PaperExecutor, logger) -> None:
        self.cfg = cfg
        self.executor = executor
        self.logger = logger

    def daily_stop_hit(self, mark_prices: dict[str, float]) -> bool:
        pnl_pct = self.executor.pnl_pct(mark_prices)
        return pnl_pct <= -abs(self.cfg.daily_stop_pct)

    def regime_multiplier(self, regime: str) -> float:
        if regime == "trend":
            return self.cfg.trend_regime_multiplier
        if regime == "chop":
            return self.cfg.chop_regime_multiplier
        return self.cfg.flat_regime_multiplier

    def size_intent(
        self,
        intent: TradeIntent,
        book: BookState,
        consensus: ConsensusPrediction,
        mark_prices: dict[str, float],
    ) -> TradeIntent | None:
        if self.daily_stop_hit(mark_prices):
            self.logger.warning(
                "daily stop triggered",
                event="risk_daily_stop_triggered",
                fields={"pnl_pct": self.executor.pnl_pct(mark_prices)},
            )
            return None

        if intent.action == "SELL":
            held = self.executor.position(intent.asset_id)
            shares = min(intent.shares, max(held, 0.0))
            if shares <= 0:
                return None
            intent.shares = shares
            return intent

        ask = book.best_ask
        if ask is None or ask.price <= 0:
            return None

        equity = self.executor.equity(mark_prices)
        base_notional = equity * self.cfg.max_capital_per_trade_pct * self.regime_multiplier(consensus.regime)
        shares = base_notional / ask.price
        shares = min(shares, ask.size)
        max_allowed = max(self.cfg.max_asset_position_shares - self.executor.position(intent.asset_id), 0.0)
        shares = min(shares, max_allowed)
        if shares <= 0:
            return None
        intent.shares = shares
        return intent
