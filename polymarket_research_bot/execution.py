from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping

from .models import FillResult, PositionSnapshot, TradeIntent, now_ms


@dataclass(slots=True)
class AccountState:
    cash_usd: float
    positions: Dict[str, float]
    starting_cash_usd: float


class PaperExecutor:
    def __init__(
        self,
        starting_cash_usd: float,
        fee_bps: float,
        reserve_cash_pct: float,
        allow_short_inventory: bool,
        logger,
    ) -> None:
        self.state = AccountState(
            cash_usd=starting_cash_usd,
            positions={},
            starting_cash_usd=starting_cash_usd,
        )
        self.fee_bps = fee_bps
        self.reserve_cash_pct = reserve_cash_pct
        self.allow_short_inventory = allow_short_inventory
        self.logger = logger

    def position(self, asset_id: str) -> float:
        return self.state.positions.get(asset_id, 0.0)

    def mark_price(self, asset_id: str, mark_prices: Mapping[str, float]) -> float:
        return float(mark_prices.get(asset_id, 0.0))

    def equity(self, mark_prices: Mapping[str, float]) -> float:
        value = self.state.cash_usd
        for asset_id, shares in self.state.positions.items():
            value += shares * self.mark_price(asset_id, mark_prices)
        return value

    def pnl_pct(self, mark_prices: Mapping[str, float]) -> float:
        eq = self.equity(mark_prices)
        return (eq - self.state.starting_cash_usd) / self.state.starting_cash_usd

    def snapshot_positions(self, mark_prices: Mapping[str, float]) -> list[PositionSnapshot]:
        out: list[PositionSnapshot] = []
        for asset_id, shares in sorted(self.state.positions.items()):
            mark = self.mark_price(asset_id, mark_prices)
            out.append(
                PositionSnapshot(
                    asset_id=asset_id,
                    shares=shares,
                    mark_price=mark,
                    market_value=shares * mark,
                )
            )
        return out

    def execute(self, intent: TradeIntent, mark_prices: Mapping[str, float]) -> FillResult:
        fee_multiplier = self.fee_bps / 10_000.0
        filled_at_ms = now_ms()
        gross_value = intent.shares * intent.price
        fee = gross_value * fee_multiplier
        if intent.action == "BUY":
            min_cash_to_keep = self.state.starting_cash_usd * self.reserve_cash_pct
            total_cost = gross_value + fee
            if total_cost <= 0:
                return FillResult(False, "invalid_cost", 0.0, 0.0, self.state.cash_usd, self.equity(mark_prices), filled_at_ms)
            if self.state.cash_usd - total_cost < min_cash_to_keep:
                return FillResult(False, "reserve_cash_block", 0.0, 0.0, self.state.cash_usd, self.equity(mark_prices), filled_at_ms)
            self.state.cash_usd -= total_cost
            self.state.positions[intent.asset_id] = self.position(intent.asset_id) + intent.shares
        elif intent.action == "SELL":
            current = self.position(intent.asset_id)
            if not self.allow_short_inventory and current + 1e-9 < intent.shares:
                return FillResult(False, "insufficient_inventory", 0.0, 0.0, self.state.cash_usd, self.equity(mark_prices), filled_at_ms)
            self.state.cash_usd += gross_value - fee
            next_position = current - intent.shares
            if abs(next_position) < 1e-9:
                self.state.positions.pop(intent.asset_id, None)
            else:
                self.state.positions[intent.asset_id] = next_position
        else:
            return FillResult(False, "unknown_action", 0.0, 0.0, self.state.cash_usd, self.equity(mark_prices), filled_at_ms)

        equity_after = self.equity(mark_prices)
        result = FillResult(
            accepted=True,
            reason="filled",
            filled_shares=intent.shares,
            avg_price=intent.price,
            cash_after=self.state.cash_usd,
            equity_after=equity_after,
            filled_at_ms=filled_at_ms,
        )
        self.logger.info(
            "paper fill",
            event="paper_fill",
            fields={
                "asset_id": intent.asset_id,
                "action": intent.action,
                "shares": intent.shares,
                "price": intent.price,
                "edge_bps": intent.edge_bps,
                "cash_after": self.state.cash_usd,
                "equity_after": equity_after,
                "reason": intent.rationale,
            },
        )
        return result
