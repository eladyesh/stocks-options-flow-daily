# Operations

## Schedule and repository ownership

The sole scheduler is `.github/workflows/options.yml` in `eladyesh/stocks-options-flow-daily`. It starts at `47 7 * * *`, timezone `Asia/Jerusalem`, on the default branch `main`. Reports send immediately after research, with no 08:00 hold. Nothing is called from the screener or news repositories. Keep the repository public for this configured hosted-runner workflow.

The two existing Actions secret names are `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. They are injected only into the verify/send steps. A bot-self ID, malformed token or non-private destination fails before research. The sender never logs tokens, chat IDs or raw Telegram error bodies. Only fixed, allowlisted error guidance is printed.

## State and repeat protection

`state/occ/` stores validated dated aggregate inputs; `state/sides/` stores final-candidate call/put history; `state/chains/` stores minimal volume/OI contract observations. Research history is pruned after 20 calendar days. Raw source bodies are not retained as artifacts.

`state/pending_report.json` freezes the report for a US session. `state/delivery.json` stores its content fingerprint, a keyed destination binding, cursor and confirmed message receipts. These files are ignored by git and cached in this repository using per-run/attempt cache keys. Reports are split to below Telegram's UTF-16 message limit and sent serially.

Concurrent reports share one concurrency group and do not cancel an in-flight delivery. Completed session delivery returns zero new messages, including weekend reruns. A partly delivered frozen report resumes only after confirmed messages. A changed destination, changed report or ambiguous prior send is blocked for review.

Explicit structured Telegram rejection (for example `ok:false`, HTTP 403) leaves the cursor unchanged and clears the in-flight marker, allowing a retry after configuration is fixed. A timeout, malformed receipt, wrong chat receipt, unstructured HTTP error or connection loss remains uncertain. There is no automatic blind retry of a send.

GitHub caches are not durable transactional storage. If the state cache is lost, expired or manually removed, check the actual chat before another same-session send. Do not delete receipts just to force a green run. This design mitigates duplicates while the cache is intact; it cannot promise exactly-once delivery after loss of state.

## Validation and failure handling

| Symptom | Action |
|---|---|
| Secret missing / destination is the bot itself | Update this repository's Actions secrets. Use the private `message.chat.id` from `getUpdates`, after pressing Start in the bot. |
| Telegram 403 | Follow the fixed error hint: unblock/start the bot or correct the private chat. Explicit rejections can retry the same frozen report. |
| Uncertain delivery | Inspect the last numbered message and receipt cursor before manually recovering the private cache. Preserve the exact pending report. Do not blindly rerun/reset. |
| OCC not yet published | Coverage reports the missing session/baseline. Dated Cboe chain activity can still produce a limited report, with unavailable comparisons labelled unknown. |
| Both discovery routes down / no same-session chain | The run fails; it does not send a normal no-news report. Inspect logs/coverage and retry after source access recovers. |
| Some chains stale or unavailable | The report states successful/attempted counts and excludes those observations. Source failures are not zero volume. |
| Stale pending report without completed delivery | Sending refuses reports older than six hours. Review state/receipts before preparing another edition; do not silently change a partly sent report. |
| No new US market session | The cron still executes; a completed report is not sent twice. |
| Schedule stops | Inspect GitHub Actions activation/default branch and public-repository inactivity disabling. Start times are best effort. |

After code changes, run the offline suite, inspect a full live no-send artifact, then explicitly request/authorize delivery. The current setup task includes user authorization for the initial live Telegram test. Synthetic tests and smoke reports cannot be sent by `options_flow send`.

All parsing and financial arithmetic tests are offline. Live verification is a separate deliberate workflow; normal push/PR tests never read Telegram secrets or send messages. Source requests are bounded, rate limited and use up to four workers. Optional Nasdaq earnings/dividend requests use a 15-second socket timeout, one attempt and concurrent collection. Unavailable event context stays unknown and does not block a report with verified options evidence. Core market-data requests retain a 35-second timeout and at most two attempts. Authentication failures/rate limits are not bypassed.
