from __future__ import annotations

from dataclasses import replace
from typing import Optional

from .config import MarketConfig, StrategyConfig
from .execution import PaperExecutor
from .models import BookState, ConsensusPrediction, TradeIntent, now_ms
from .polymarket_ws import OrderBookStore


class SignalEngine:
    def __init__(
        self,
        market_cfg: MarketConfig,
        strategy_cfg: StrategyConfig,
        executor: PaperExecutor,
        logger,
    ) -> None:
        self.market_cfg = market_cfg
        self.cfg = strategy_cfg
        self.executor = executor
        self.logger = logger
        self.last_fire_ms: dict[str, int] = {}

    def _book_is_tradeable(self, book: Optional[BookState], now_ts_ms: int, book_stale_ms: int) -> bool:
        if not book:
            return False
        if not book.best_bid or not book.best_ask:
            return False
        if (now_ts_ms - book.last_update_ms) > book_stale_ms:
            return False
        spread_bps = book.spread_bps()
        if spread_bps is None or spread_bps > self.cfg.max_midpoint_spread_bps:
            return False
        return True

    def _entry_intent(
        self,
        asset_id: str,
        book: BookState,
        fair_value: float,
        now_ts_ms: int,
    ) -> Optional[TradeIntent]:
        ask = book.best_ask
        if not ask:
            return None
        if ask.size < self.cfg.min_top_level_liquidity:
            return None
        edge_bps = (fair_value - ask.price) * 10_000.0
        if edge_bps < self.cfg.edge_threshold_bps:
            return None
        last_fire = self.last_fire_ms.get(asset_id, 0)
        if now_ts_ms - last_fire < self.cfg.rearm_ms:
            return None
        return TradeIntent(
            asset_id=asset_id,
            action="BUY",
            price=ask.price,
            shares=0.0,
            edge_bps=edge_bps,
            rationale="entry_edge_gt_threshold",
            generated_at_ms=now_ts_ms,
        )

    def _exit_intent(
        self,
        asset_id: str,
        book: BookState,
        fair_value: float,
        now_ts_ms: int,
    ) -> Optional[TradeIntent]:
        bid = book.best_bid
        held = self.executor.position(asset_id)
        if not bid or held <= 0:
            return None
        if bid.size < self.cfg.min_top_level_liquidity:
            return None
        edge_bps = (bid.price - fair_value) * 10_000.0
        if edge_bps < self.cfg.exit_threshold_bps:
            return None
        last_fire = self.last_fire_ms.get(asset_id, 0)
        if now_ts_ms - last_fire < self.cfg.rearm_ms:
            return None
        return TradeIntent(
            asset_id=asset_id,
            action="SELL",
            price=bid.price,
            shares=held,
            edge_bps=edge_bps,
            rationale="exit_price_gt_fair_value",
            generated_at_ms=now_ts_ms,
        )

    def evaluate(
        self,
        consensus: ConsensusPrediction,
        store: OrderBookStore,
        book_stale_ms: int,
    ) -> Optional[TradeIntent]:
        now_ts_ms = now_ms()
        yes_book = store.get(self.market_cfg.yes_token_id)
        no_book = store.get(self.market_cfg.no_token_id)
        if not self._book_is_tradeable(yes_book, now_ts_ms, book_stale_ms):
            return None
        if not self._book_is_tradeable(no_book, now_ts_ms, book_stale_ms):
            return None
        assert yes_book is not None and no_book is not None

        fair_yes = consensus.probability_yes
        fair_no = 1.0 - consensus.probability_yes

        exit_yes = self._exit_intent(self.market_cfg.yes_token_id, yes_book, fair_yes, now_ts_ms)
        exit_no = self._exit_intent(self.market_cfg.no_token_id, no_book, fair_no, now_ts_ms)
        entry_yes = self._entry_intent(self.market_cfg.yes_token_id, yes_book, fair_yes, now_ts_ms)
        entry_no = self._entry_intent(self.market_cfg.no_token_id, no_book, fair_no, now_ts_ms)

        intents = [x for x in [exit_yes, exit_no, entry_yes, entry_no] if x is not None]
        if not intents:
            return None
        best = max(intents, key=lambda x: x.edge_bps)
        return replace(best)

    def mark_fired(self, asset_id: str, fired_ms: int | None = None) -> None:
        self.last_fire_ms[asset_id] = fired_ms or now_ms()
