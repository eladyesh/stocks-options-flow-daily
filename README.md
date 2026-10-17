# Stocks Options Flow Daily

Daily research on unusual call and put activity in US stocks and ADRs, with English reports and Telegram delivery. All repository documentation, application messages and reports are in English.

Based on the [options-flow skill](https://github.com/eladyesh/stock-market-skills/tree/main/skills/options-flow). This project runs independently in this repository, with a daily cron at **07:47 Israel time (`Asia/Jerusalem`)**, including daylight-saving adjustments. Delivery starts when the scan finishes. GitHub Actions start times are best effort.

## What the scan covers

- Common stocks and ADRs from the official Nasdaq Trader directories, excluding identified ETFs, funds, preferred shares, units, warrants and acquisition companies.
- Broad OCC daily equity-option activity compared with the preceding five trading sessions.
- A second discovery route using Cboe C1 contract-volume data: call leaders, put leaders and concentrated contracts. C1 represents one exchange, not the entire US options market.
- Up to 80 dated Cboe option chains within 0–365 days to expiration, producing up to 10 research candidates. Every report shows actual coverage.
- Contract side, strike, expiration, DTE, volume, available open interest, implied volatility, bid/ask, spread, underlying price and observation dates.
- Separate OCC call-volume and put-volume comparisons against their own five-session averages for the final candidates.
- Public Nasdaq earnings and dividend calendars, explicitly labelled as provider reports rather than issuer-confirmed events.

**The evidence is delayed option-chain activity.** Call volume does not establish bullish buying, and put volume does not establish bearish positioning. This project does not identify synchronized executions, institutions, confirmed sweeps or opening/closing trades. It never labels volume multiplied by the last price as premium paid. The chain source does not document the OI date, contract multiplier or deliverable, so those fields remain unknown.

## GitHub setup

In **`eladyesh/stocks-options-flow-daily`**, open **Settings → Secrets and variables → Actions** and configure:

| Secret | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | The complete bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Your private `message.chat.id` from `getUpdates`, after pressing Start in the bot |

`getMe` returns the bot's own ID, not your chat ID. The application checks this before research. Never store secrets in code, reports or logs. No paid market-data API key is required.

Open **Actions → Daily Options Flow → Run workflow**:

| Mode | `smoke` | `send_telegram` | Result |
|---|---|---|---|
| Limited source check | `true` | `false` | Up to 6 chains, report artifact, no messages |
| Full report without delivery | `false` | `false` | Up to 80 chains, report artifact |
| Full report and delivery | `false` | `true` | Validate the chat, scan and send after completion |
| Daily cron | Automatic | Automatic | Scan and delivery starting at 07:47 Israel time |

Smoke mode never sends messages, even when `send_telegram` is selected. The workflow runs only on `main` in this configured public repository.

## Reports and saved state

Each report run produces an artifact containing:

- `report.md` — English contract table and research notes for each stock.
- `report.json` — Structured observations, provenance, timestamps, limitations and coverage.
- `telegram.txt` — English message content before splitting into numbered parts.
- `coverage.json` — Source routes checked, including errors, restrictions and stale observations that were rejected.

The cron executes daily, but delivery occurs once per **completed US trading session**. Weekend or holiday reruns do not resend an already delivered session. `exchange-calendars` handles market holidays and early closes, with a 20-minute post-close buffer for delayed data.

OCC history covers five previous sessions from the first run. Contract-specific history builds over subsequent runs; no historical ratio is shown until five comparable observations exist. Zero open interest makes V/OI undefined, not infinite. V/OI without a verified OI date is only a descriptive flag.

Source history and delivery receipts are cached between full delivery runs. The first run has to build that history and may take longer. Receipt checks mitigate repeat delivery while the cache is intact; see the operations guide for failures or cache loss.

## Local usage

Use Python 3.12:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m options_flow run --smoke
python -m options_flow run
python -m options_flow run --symbols AAPL,MSFT,NVDA
```

Local research does not send messages by default. After securely configuring the secret environment variables:

```bash
python -m options_flow verify-telegram
python -m options_flow run --prepare-delivery
python -m options_flow send
```

Thresholds, the chain limit and an optional watchlist are in `config.json`. The report language is English. Changing a watchlist does not change the Telegram destination. See [methodology](docs/METHODOLOGY.md) and [operations and recovery](docs/OPERATIONS.md) for details.
