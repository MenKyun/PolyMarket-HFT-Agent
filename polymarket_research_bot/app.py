from __future__ import annotations

import asyncio
from typing import Any, Optional

import aiohttp

from .config import AppConfig
from .execution import PaperExecutor
from .models import ConsensusPrediction, PredictionPoint, now_ms
from .polymarket_ws import OrderBookStore, PolymarketMarketWS
from .prediction_sources import HttpProbabilitySource, PredictionConsensus
from .risk import RiskManager
from .strategy import SignalEngine


class ResearchBotApp:
    def __init__(self, cfg: AppConfig, logger) -> None:
        self.cfg = cfg
        self.logger = logger
        self.store = OrderBookStore([cfg.market.yes_token_id, cfg.market.no_token_id])
        self.latest_consensus: Optional[ConsensusPrediction] = None
        self.event_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=cfg.runtime.queue_maxsize)
        self.executor = PaperExecutor(
            starting_cash_usd=cfg.account.starting_cash_usd,
            fee_bps=cfg.risk.fee_bps,
            reserve_cash_pct=cfg.risk.reserve_cash_pct,
            allow_short_inventory=cfg.risk.allow_short_inventory,
            logger=logger,
        )
        self.risk = RiskManager(cfg.risk, self.executor, logger)
        self.signal_engine = SignalEngine(cfg.market, cfg.strategy, self.executor, logger)
        self.consensus_engine = PredictionConsensus(
            min_sources=cfg.strategy.min_sources,
            max_disagreement_bps=cfg.strategy.max_source_disagreement_bps,
            fast_alpha=cfg.strategy.fast_ema_alpha,
            slow_alpha=cfg.strategy.slow_ema_alpha,
            trend_window=cfg.strategy.trend_window,
            trend_threshold_bps=cfg.strategy.trend_threshold_bps,
        )

    def mark_prices(self) -> dict[str, float]:
        marks: dict[str, float] = {}
        for asset_id in (self.cfg.market.yes_token_id, self.cfg.market.no_token_id):
            book = self.store.get(asset_id)
            if book and book.midpoint is not None:
                marks[asset_id] = book.midpoint
            elif book and book.best_bid is not None:
                marks[asset_id] = book.best_bid.price
            elif book and book.best_ask is not None:
                marks[asset_id] = book.best_ask.price
        return marks

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.runtime.health_interval_sec)
            marks = self.mark_prices()
            equity = self.executor.equity(marks)
            positions = [
                {
                    'asset_id': p.asset_id,
                    'shares': p.shares,
                    'mark_price': p.mark_price,
                    'market_value': p.market_value,
                }
                for p in self.executor.snapshot_positions(marks)
            ]
            fields = {
                "equity": equity,
                "pnl_pct": self.executor.pnl_pct(marks),
                "cash_usd": self.executor.state.cash_usd,
                "positions": positions,
                "queue_size": self.event_queue.qsize(),
            }
            if self.latest_consensus:
                fields.update(
                    {
                        "consensus_probability_yes": self.latest_consensus.probability_yes,
                        "consensus_regime": self.latest_consensus.regime,
                        "valid_sources": self.latest_consensus.valid_sources,
                        "disagreement_bps": self.latest_consensus.disagreement_bps,
                    }
                )
            self.logger.info("health", event="health", fields=fields)

    async def _producers(self, session: aiohttp.ClientSession) -> list[asyncio.Task]:
        tasks: list[asyncio.Task] = []
        ws_client = PolymarketMarketWS(session, self.cfg.market, self.logger, self.store)
        tasks.append(asyncio.create_task(ws_client.run(self.event_queue), name="polymarket_market_ws"))
        for source_cfg in self.cfg.sources:
            if not source_cfg.enabled:
                continue
            source = HttpProbabilitySource(session, source_cfg, self.logger)
            tasks.append(asyncio.create_task(source.run(self.event_queue), name=f"source:{source_cfg.name}"))
        tasks.append(asyncio.create_task(self._health_loop(), name="health"))
        return tasks

    async def _maybe_trade(self) -> None:
        if not self.latest_consensus:
            return
        if not self.store.ready(self.cfg.market.yes_token_id, self.cfg.market.no_token_id):
            return
        marks = self.mark_prices()
        intent = self.signal_engine.evaluate(
            consensus=self.latest_consensus,
            store=self.store,
            book_stale_ms=self.cfg.market.book_stale_ms,
        )
        if not intent:
            return

        book = self.store.get(intent.asset_id)
        if not book:
            return
        sized = self.risk.size_intent(intent, book, self.latest_consensus, marks)
        if not sized:
            return

        decision_latency_ms = now_ms() - max(self.latest_consensus.received_at_ms, book.last_update_ms)
        self.logger.info(
            "trade intent",
            event="trade_intent",
            fields={
                "asset_id": sized.asset_id,
                "action": sized.action,
                "price": sized.price,
                "shares": sized.shares,
                "edge_bps": sized.edge_bps,
                "decision_latency_ms": decision_latency_ms,
                "regime": self.latest_consensus.regime,
                "probability_yes": self.latest_consensus.probability_yes,
            },
        )

        if not self.cfg.runtime.paper_mode:
            raise NotImplementedError(
                "Live execution intentionally not implemented in this package. "
                "Use the official SDK only after a separate compliance, security, and manual-control review."
            )

        result = self.executor.execute(sized, marks)
        if result.accepted:
            self.signal_engine.mark_fired(sized.asset_id, result.filled_at_ms)

    async def run(self) -> None:
        if not self.cfg.runtime.paper_mode or self.cfg.runtime.allow_live_orders:
            raise NotImplementedError(
                "This package only supports paper_mode=true. "
                "Turnkey autonomous real-money execution is intentionally omitted."
            )
        timeout = aiohttp.ClientTimeout(total=10)
        connector = aiohttp.TCPConnector(limit=0, ttl_dns_cache=300)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            tasks = await self._producers(session)
            self.logger.info(
                "app started",
                event="app_started",
                fields={
                    "paper_mode": self.cfg.runtime.paper_mode,
                    "yes_token_id": self.cfg.market.yes_token_id,
                    "no_token_id": self.cfg.market.no_token_id,
                    "sources": [s.name for s in self.cfg.sources if s.enabled],
                },
            )
            try:
                while True:
                    event = await self.event_queue.get()
                    if isinstance(event, PredictionPoint):
                        consensus = self.consensus_engine.update(event)
                        if consensus is not None:
                            self.latest_consensus = consensus
                            self.logger.info(
                                "consensus updated",
                                event="consensus_updated",
                                fields={
                                    "probability_yes": consensus.probability_yes,
                                    "valid_sources": consensus.valid_sources,
                                    "disagreement_bps": consensus.disagreement_bps,
                                    "regime": consensus.regime,
                                },
                            )
                    elif isinstance(event, dict):
                        event_type = event.get("event_type", "unknown")
                        if event_type in {"book", "best_bid_ask", "price_change", "last_trade_price"}:
                            self.logger.debug(
                                "market event",
                                event="market_event",
                                fields={"event_type": event_type},
                            )
                    await self._maybe_trade()
                    await asyncio.sleep(self.cfg.runtime.decision_loop_sleep_ms / 1000.0)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
