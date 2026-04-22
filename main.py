from __future__ import annotations

import argparse
import asyncio

try:
    import uvloop  # type: ignore
except Exception:  # pragma: no cover
    uvloop = None

from polymarket_research_bot.app import ResearchBotApp
from polymarket_research_bot.config import load_config
from polymarket_research_bot.logging_utils import setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polymarket research / paper trading bot")
    parser.add_argument("--config", required=True, help="Path to YAML config")
    return parser.parse_args()


async def amain() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    logger = setup_logging(cfg.runtime.logs_dir, cfg.runtime.log_level)
    app = ResearchBotApp(cfg, logger)
    await app.run()


if __name__ == "__main__":
    if uvloop is not None:
        uvloop.install()
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
