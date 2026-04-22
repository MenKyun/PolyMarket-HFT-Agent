from __future__ import annotations

import asyncio
import json
from typing import Dict, Optional

import aiohttp

from .config import MarketConfig
from .models import BookState, now_ms


class OrderBookStore:
    def __init__(self, asset_ids: list[str]) -> None:
        self.books: Dict[str, BookState] = {asset_id: BookState(asset_id=asset_id) for asset_id in asset_ids}

    def get(self, asset_id: str) -> Optional[BookState]:
        return self.books.get(asset_id)

    def ready(self, yes_asset_id: str, no_asset_id: str) -> bool:
        yes = self.books.get(yes_asset_id)
        no = self.books.get(no_asset_id)
        if not yes or not no:
            return False
        return yes.best_bid is not None and yes.best_ask is not None and no.best_bid is not None and no.best_ask is not None

    def apply_message(self, payload: dict) -> None:
        event_type = payload.get("event_type", "")
        ts_ms = int(payload.get("timestamp", now_ms()))
        if event_type == "book":
            asset_id = payload["asset_id"]
            book = self.books.setdefault(asset_id, BookState(asset_id=asset_id))
            book.replace_from_snapshot(payload.get("bids", []), payload.get("asks", []), ts_ms, event_type)
            return
        if event_type == "best_bid_ask":
            asset_id = payload["asset_id"]
            book = self.books.setdefault(asset_id, BookState(asset_id=asset_id))
            book.set_best_bid_ask(float(payload["best_bid"]), float(payload["best_ask"]), ts_ms, event_type)
            return
        if event_type == "price_change":
            for item in payload.get("price_changes", []):
                asset_id = item["asset_id"]
                book = self.books.setdefault(asset_id, BookState(asset_id=asset_id))
                book.apply_price_level(
                    side=item["side"],
                    price=float(item["price"]),
                    size=float(item["size"]),
                    ts_ms=ts_ms,
                    event_type=event_type,
                )
                best_bid = float(item.get("best_bid", 0.0))
                best_ask = float(item.get("best_ask", 0.0))
                if best_bid > 0.0 or best_ask > 0.0:
                    book.set_best_bid_ask(best_bid, best_ask, ts_ms, event_type)
            return
        if event_type == "tick_size_change":
            asset_id = payload["asset_id"]
            book = self.books.setdefault(asset_id, BookState(asset_id=asset_id))
            book.last_update_ms = ts_ms
            book.last_event_type = event_type
            return
        if event_type == "last_trade_price":
            asset_id = payload["asset_id"]
            book = self.books.setdefault(asset_id, BookState(asset_id=asset_id))
            book.last_update_ms = ts_ms
            book.last_event_type = event_type


class PolymarketMarketWS:
    def __init__(self, session: aiohttp.ClientSession, cfg: MarketConfig, logger, store: OrderBookStore) -> None:
        self.session = session
        self.cfg = cfg
        self.logger = logger
        self.store = store
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def _send_ping_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(self.cfg.ws_ping_interval_sec)
            await ws.send_str("PING")
            self.logger.debug("market ws ping", event="market_ws_ping")

    async def run(self, queue: asyncio.Queue[dict]) -> None:
        subscribe_payload = {
            "assets_ids": [self.cfg.yes_token_id, self.cfg.no_token_id],
            "type": "market",
            "custom_feature_enabled": self.cfg.custom_feature_enabled,
        }
        while not self._stop_event.is_set():
            ping_task: Optional[asyncio.Task] = None
            try:
                async with self.session.ws_connect(self.cfg.market_ws_url, heartbeat=0) as ws:
                    await ws.send_json(subscribe_payload)
                    self.logger.info(
                        "market ws connected",
                        event="market_ws_connected",
                        fields={
                            "market_ws_url": self.cfg.market_ws_url,
                            "yes_token_id": self.cfg.yes_token_id,
                            "no_token_id": self.cfg.no_token_id,
                        },
                    )
                    ping_task = asyncio.create_task(self._send_ping_loop(ws))
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            if msg.data == "PONG":
                                continue
                            payload = json.loads(msg.data)
                            if isinstance(payload, dict) and payload.get("event_type"):
                                self.store.apply_message(payload)
                                await queue.put(payload)
                            continue
                        if msg.type == aiohttp.WSMsgType.ERROR:
                            raise RuntimeError(f"WebSocket error: {ws.exception()}")
                        if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.warning(
                    "market ws disconnected",
                    event="market_ws_disconnected",
                    fields={"error": repr(exc)},
                )
                await asyncio.sleep(1.0)
            finally:
                if ping_task:
                    ping_task.cancel()
                    with contextlib.suppress(Exception):
                        await ping_task


import contextlib
