# Polymarket research / paper trading bot

This package is a **research and paper-trading framework** for binary Polymarket contracts.
It is designed to:

- subscribe to the official Polymarket **market** WebSocket for the YES and NO token IDs;
- poll multiple external forecast sources that already emit a normalized `probability_yes` in `[0, 1]`;
- compute a consensus signal with cross-source disagreement checks;
- compare that fair value with the current best bid / ask on Polymarket;
- size entries dynamically based on regime (`trend` vs `chop`) and hard risk limits;
- log every event, consensus update, trade intent, paper fill, and health snapshot as JSON lines.

## Important

This package intentionally supports **paper mode only**.
Turnkey autonomous live execution is **not implemented** here.

If you later build a live executor, you should do it separately with:

- manual approval gates;
- exchange / venue compliance review;
- secrets management;
- region checks;
- explicit rate limiting;
- chaos testing and kill switches.

## Why the external sources point to your own gateway

For a production workflow, your upstream model should already convert BTC inputs into a contract-specific `probability_yes`.
That mapping is market-specific, and it is usually safer to normalize it in your own service rather than inside the execution loop.

Example payload expected from each source:

```json
{
  "data": {
    "probability_yes": 0.5375
  },
  "timestamp": 1760000000000
}
```

Or:

```json
{
  "probability_yes": 0.5375,
  "timestamp": 1760000000000
}
```

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configure

1. Copy `config.example.yaml` to `config.yaml`.
2. Fill in the YES and NO `token_id` values for the target binary market.
3. Point each source at your own forecast endpoint.
4. Export any required API keys:

```bash
export CRYPTOQUANT_API_KEY='...'
export TRADINGVIEW_API_KEY='...'
```

## Run

```bash
python main.py --config config.yaml
```

## Logs

JSON logs are written to stdout and `./logs/bot.jsonl`.
Each record includes a timestamp, event name, and structured fields.

## Architecture

- `polymarket_ws.py`: official market WebSocket client with `PING` keepalive.
- `prediction_sources.py`: async HTTP pollers for external forecast services.
- `strategy.py`: fair-value comparison and signal generation.
- `risk.py`: size controls, regime multipliers, and daily stop.
- `execution.py`: paper execution and account state.
- `app.py`: orchestration.

## Notes on extending

If you extend this into a live stack, use the **official Polymarket SDK** for authentication and order submission, not an ad hoc signer.
Keep the live order path physically separated from research code.
