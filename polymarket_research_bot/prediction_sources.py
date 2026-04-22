from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Iterable, Optional

import aiohttp

from .config import SourceConfig
from .models import ConsensusPrediction, EmaPair, PredictionPoint, RollingWindow, now_ms


class JsonPathError(KeyError):
    pass



def extract_json_path(payload: Dict[str, Any], path: str) -> Any:
    cur: Any = payload
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise JsonPathError(path)
    return cur


class HttpProbabilitySource:
    def __init__(self, session: aiohttp.ClientSession, cfg: SourceConfig, logger) -> None:
        self.session = session
        self.cfg = cfg
        self.logger = logger
        self.latest: Optional[PredictionPoint] = None
        self._stopped = asyncio.Event()

    def stop(self) -> None:
        self._stopped.set()

    async def run(self, queue: asyncio.Queue[PredictionPoint]) -> None:
        while not self._stopped.is_set():
            started_ms = now_ms()
            try:
                headers = dict(self.cfg.headers)
                if self.cfg.api_key_env:
                    secret = os.getenv(self.cfg.api_key_env)
                    if secret:
                        headers[self.cfg.api_key_header] = f"{self.cfg.api_key_prefix}{secret}"
                timeout = aiohttp.ClientTimeout(total=self.cfg.request_timeout_ms / 1000.0)
                async with self.session.get(self.cfg.url, headers=headers, timeout=timeout) as response:
                    response.raise_for_status()
                    payload = await response.json()
                probability_yes = float(extract_json_path(payload, self.cfg.probability_json_path))
                probability_yes = min(max(probability_yes, 0.0), 1.0)
                received_at_ms = now_ms()
                source_ts_ms = None
                for key in ("timestamp", "ts", "time", "updated_at_ms"):
                    try:
                        value = extract_json_path(payload, key)
                        source_ts_ms = int(value)
                        break
                    except Exception:
                        continue
                point = PredictionPoint(
                    source=self.cfg.name,
                    probability_yes=probability_yes,
                    received_at_ms=received_at_ms,
                    source_ts_ms=source_ts_ms,
                    source_latency_ms=received_at_ms - started_ms,
                    weight=self.cfg.weight,
                )
                self.latest = point
                await queue.put(point)
                self.logger.info(
                    "prediction source tick",
                    event="prediction_source_tick",
                    fields={
                        "source": self.cfg.name,
                        "probability_yes": probability_yes,
                        "latency_ms": point.source_latency_ms,
                    },
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.warning(
                    "prediction source error",
                    event="prediction_source_error",
                    fields={"source": self.cfg.name, "error": repr(exc)},
                )
            await asyncio.sleep(self.cfg.poll_interval_ms / 1000.0)


class PredictionConsensus:
    def __init__(
        self,
        min_sources: int,
        max_disagreement_bps: float,
        fast_alpha: float,
        slow_alpha: float,
        trend_window: int,
        trend_threshold_bps: float,
    ) -> None:
        self.min_sources = min_sources
        self.max_disagreement_bps = max_disagreement_bps
        self.ema_pair = EmaPair(fast_alpha=fast_alpha, slow_alpha=slow_alpha)
        self.window = RollingWindow(maxlen=trend_window)
        self.latest_by_source: Dict[str, PredictionPoint] = {}
        self.trend_threshold_bps = trend_threshold_bps

    def update(self, point: PredictionPoint) -> Optional[ConsensusPrediction]:
        self.latest_by_source[point.source] = point
        active = list(self.latest_by_source.values())
        if len(active) < self.min_sources:
            return None
        total_weight = sum(max(p.weight, 0.0) for p in active)
        if total_weight <= 0:
            return None
        weighted_prob = sum(p.probability_yes * p.weight for p in active) / total_weight
        min_prob = min(p.probability_yes for p in active)
        max_prob = max(p.probability_yes for p in active)
        disagreement_bps = (max_prob - min_prob) * 10_000.0
        if disagreement_bps > self.max_disagreement_bps:
            return None
        self.window.append(weighted_prob)
        fast, slow = self.ema_pair.update(weighted_prob)
        trend_strength_bps = abs(fast - slow) * 10_000.0
        regime = "trend" if trend_strength_bps >= self.trend_threshold_bps else "chop"
        return ConsensusPrediction(
            probability_yes=weighted_prob,
            valid_sources=len(active),
            disagreement_bps=disagreement_bps,
            received_at_ms=point.received_at_ms,
            slow_ema=slow,
            fast_ema=fast,
            regime=regime,
        )
