# Methodology and evidence contract

Implemented from the user's [options-flow skill](https://github.com/eladyesh/stock-market-skills/blob/main/skills/options-flow/SKILL.md), source blob `1b4923ec0b3a9ae352909d98be139facfaf9bb4d`, and its [source map](https://github.com/eladyesh/stock-market-skills/blob/main/skills/options-flow/references/sources.md), blob `f570a440875b2ea57f4f616dc00782208a8c0487`, read on 2026-10-17. This project implements the skill's expressly permitted **limited chain-level result**. It does not claim to implement a paid trade tape or LLM-based investigation of every market article.

## Discovery and coverage

1. Download the two official Nasdaq Trader symbol directories. Keep identifiable common/ordinary shares and ADRs, excluding directory-marked ETFs and test issues and name-marked preferreds, funds, units, warrants and acquisition companies. Name-based legal-class screening is imperfect and is disclosed. Do not treat every option underlying as an operating stock.
2. Download OCC's dated daily equity-option trade-volume records for the latest completed XNYS session and five preceding sessions. Aggregate the three reported account quantities by underlying. These account-volume sums are a discovery measure; they are **not relabelled as single-count contract volume**. Relative volume uses the same fields and source each day. Missing symbols/dates are unknown, never imputed as zero.
3. Independently inspect Cboe C1's public contract-volume CSV. Its HTTP Last-Modified dates the file update, not a guaranteed tape timestamp. It is a discovery lead, and a finalist still needs a same-session chain. C1 is one exchange; its counts are never presented as total US activity.
4. Interleave OCC relative-volume leaders, C1 call leaders, put leaders and concentrated contracts, plus a small deterministic rotating sample. Investigate up to 80 chains, not every discovered name. A supplied list replaces the discovery selection; unverified/ineligible symbols and oversized lists are rejected explicitly.
5. Check actual chain identity, equity class, underlying last-trade date, contract root, expiration and positive-volume contract last-trade date. Reject stale observations, duplicate identifiers, malformed values and unsupported adjusted roots. Retain actual exclusion and coverage counts.

## Calculations

| Measure | Definition and limits |
|---|---|
| Underlying relative activity | Current OCC customer + firm + market-maker quantity divided by the mean for the same underlying over exactly five previous sessions. Baseline must be at least 1,000 account-volume units; current discovery floor is 10,000. This ratio does not identify buyers. |
| Call/put relative activity | For final candidates, repeat OCC's dated volume query for each of the six sessions; compare each side's account-volume sum with its own five-session average. All account types and option roots for that underlying are retained consistently. These totals are not added to chain counts. |
| Chain put/call ratio | Eligible put contracts divided by eligible call contracts in the same provider snapshot, same session and configured 0–365 DTE range. A zero denominator is undefined. |
| Snapshot V/OI | Current dated contract volume divided by positive OI in the snapshot. The source does not document the OI date, so this is a descriptive lead, not a verified volume/prior-session-OI signal. |
| Same-contract relative volume | Only after five prior comparable cached contract observations exist: current volume divided by their mean. Missing history gives unknown; observed zero volume remains zero. No z-score, IV rank or percentile is invented. |
| Spread and moneyness | (Ask−bid)/midpoint only for valid non-crossed quotes; strike/reported underlying price. Quotes are not assumed synchronized to executions. |
| Premium | Unknown. No executed-trade tape or independently documented contract multiplier/deliverable is available. Volume × last price is never labelled premium paid; even an activity proxy is withheld without a documented multiplier. |

The default flags are volume above 500 contracts and either a comparable underlying/contract ratio above 3 or a snapshot V/OI above 3. The last flag is explicitly lower-quality descriptive evidence. Ranked priority favors comparable historical volume evidence, then the size of the observation. It is not a probability of profit. Broad volume comparisons do not control statistically for weekday, monthly expiration or event regimes; finalist notes flag expiration/event alternatives.

## Direction, structure and confidence

All automated observations are labelled **direction-unknown**. Call-heavy, put-heavy and mixed describe contract counts only. No anonymous investor identity, aggressor side, opening/closing status, sweep or spread linkage is invented. A call can be sold; a put may hedge stock; separate strikes can be legs of one position.

Evidence confidence is Medium for dated primary-source activity with a comparable volume baseline, Low when it rests on undated OI or a wide/unusable quote. There is no High-confidence directional call. A valid result can contain fewer than ten candidates or no match. The next step is documented deliverable/OI confirmation and, if directional interpretation is needed, a synchronized execution tape.

OI dates and next-session OI changes remain unknown because the public chain response does not provide a verified OI as-of date. Storing tomorrow's snapshot does not justify claiming a specific print opened a position.

## Event context and provenance

The Nasdaq earnings calendar is queried from the observation session through eight following calendar days. Rows are labelled provider calendar reports, not issuer-confirmed catalysts. Dividend data retains its reported date; a historical last ex-date is not presented as a future event. Missing/blocked routes remain unknown. The app does not silently substitute article sentiment for contract evidence.

Each source record retains its URL, retrieval timestamp, requested/observed session, evidence/access label and source-specific limitations. Contracts retain currency, units, last-trade timestamp, spot reference, and inherited retrieval provenance. The Cboe dashboard labels last-trade times in ET; the JSON top-level refresh timestamp has no documented timezone and is preserved as raw text rather than being falsely marked UTC.

## Source references

- [OCC trade-volume batch interface](https://www.theocc.com/market-data/market-data-reports/other-market-data-info/batch-processing/trade-volume-download-batch-processing)
- [OCC call/put volume query interface](https://www.theocc.com/market-data/market-data-reports/other-market-data-info/batch-processing/volume-query-batch-processing)
- [Cboe C1 symbol data](https://www.cboe.com/markets/us/options/market-statistics/symbol-data/cone)
- [Cboe delayed quotes](https://www.cboe.com/delayed_quotes/AAPL/quote_table)
- [Nasdaq earnings calendar](https://www.nasdaq.com/market-activity/earnings)
- [Nasdaq Trader symbol directory definitions](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs)
- [exchange-calendars](https://github.com/gerrymanoim/exchange_calendars)

Public Cboe/Nasdaq JSON routes are website interfaces, not a guaranteed vendor API contract. They are validated at runtime. HTTP restrictions are reported; the client does not evade them, use paid exports, create subscriptions or fabricate API keys.
