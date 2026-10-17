from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import threading
from urllib.parse import quote

from .analysis import baseline, choose_symbols, evaluate_chains
from .core import UTC, atomic_json, completed_sessions, iso, ratio, read_json
from .http import Client
from .sources import (DIRECTORIES, CBOE_SYMBOLS, aliases, chain_url, occ_url, occ_sides_url,
                      parse_directory, parse_occ_xml, parse_occ_sides, parse_cboe_symbols, parse_chain)


class Collector:
    def __init__(self, client=None):
        self.client = client or Client()
        self.coverage = []
        self.lock = threading.Lock()

    def fetch(self, name, url, parser, *, session=None):
        entry = {"source":name, "url":url, "requested_session":session, "retrieved_at":iso()}
        try:
            download = self.client.get(url)
            value = parser(download)
            entry.update(status="ok", **download.provenance())
            if isinstance(value, dict):
                value["source"] = dict(entry)
            result = value
        except Exception as exc:
            entry.update(status="unavailable", reason=f"{type(exc).__name__}: {str(exc)[:180]}")
            result = None
        with self.lock:
            self.coverage.append(entry)
        return result


def calendar_rows(download, requested_date):
    data = download.json().get("data") or {}
    observed = datetime.strptime(data.get("asOf", ""), "%a, %b %d, %Y").date().isoformat()
    if observed != requested_date:
        raise ValueError("Earnings calendar observation date mismatch")
    rows = data.get("rows")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise ValueError("Unexpected earnings calendar schema")
    return {"rows":rows, "date":requested_date}


def collect(config, state_dir, *, smoke=False, symbols=None, now=None, client=None):
    now = now or datetime.now(UTC)
    state_dir = Path(state_dir)
    sessions = completed_sessions(now)
    session, prior_sessions = sessions[-1], sessions[:-1]
    collector = Collector(client)
    workers = config["http_workers"]
    print(f"Target US session: {session}; comparing {', '.join(prior_sessions)}.", flush=True)
    names = {}
    for url in DIRECTORIES:
        result = collector.fetch("Nasdaq Trader listed securities", url, lambda d: {"names":parse_directory(d.body.decode("utf-8-sig"))})
        if result:
            names.update(result["names"])
    if not names:
        raise RuntimeError("Listing directories unavailable; cannot verify eligible stock universe")
    alias = aliases(names)
    supplied = symbols or config.get("watchlist") or []
    if supplied:
        unknown = [s for s in supplied if s not in alias]
        if unknown:
            raise ValueError("Unverified/ineligible stock symbols: " + ", ".join(unknown))
        supplied = [alias[s] for s in supplied]
        if len(supplied) > config["max_chain_symbols"]:
            raise ValueError("Watchlist exceeds configured chain limit; raise the limit explicitly")
    occ = {}

    def load_day(day):
        path = state_dir / "occ" / f"{day}.json"
        cached = read_json(path)
        if cached.get("schema") == 1 and cached.get("session") == day and cached.get("totals"):
            with collector.lock:
                collector.coverage.append({**cached["source"], "status":"cached", "cache_checked_at":iso()})
            return day, cached
        result = collector.fetch("OCC daily equity-option account volume", occ_url(day),
                                 lambda d:parse_occ_xml(d.body, day), session=day)
        if result:
            result["schema"] = 1
            atomic_json(path, result)
        return day, result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for day, result in pool.map(load_day, sessions):
            if result:
                occ[day] = result
    discovery_raw = collector.fetch("Cboe C1 contract-volume discovery", CBOE_SYMBOLS,
                                    lambda d:parse_cboe_symbols(d, session), session=session)
    discovery = {alias[s]:v for s, v in (discovery_raw or {}).get("symbols", {}).items() if s in alias}
    occ_stats = {}
    current_occ = occ.get(session, {}).get("totals", {})
    for raw, current in current_occ.items():
        if raw not in alias:
            continue
        values = [occ.get(d, {}).get("totals", {}).get(raw) for d in prior_sessions]
        average = baseline(values)
        relative = ratio(current, average) if average is not None and average >= config["minimum_occ_baseline"] else None
        occ_stats[alias[raw]] = {"raw_symbol":raw, "current":current, "baseline":average, "relative":relative,
            "baseline_sessions_available":sum(x is not None for x in values),
            "session":session, "prior_sessions":prior_sessions,
            "units":occ[session]["units"], "source":occ[session]["source"]}
    if not occ_stats and not discovery and not supplied:
        atomic_json(Path("output") / "coverage.json", collector.coverage)
        raise RuntimeError("Both broad discovery routes unavailable; refusing an unsupported normal report")
    scan_config = dict(config)
    if smoke:
        scan_config["max_chain_symbols"] = 6
    selected = choose_symbols(occ_stats, discovery, scan_config, session, supplied)
    print(f"Eligible listed stocks/ADRs: {len(names)}; OCC names: {len(occ_stats)}; Cboe discovery names: {len(discovery)}; fetching {len(selected)} chains.", flush=True)

    def get_chain(symbol):
        return collector.fetch(f"Cboe chain {symbol}", chain_url(symbol),
            lambda d:parse_chain(d.json(), symbol, session, config["max_dte"]), session=session)

    chains = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, result in enumerate(pool.map(get_chain, selected), 1):
            if result:
                for contract in result["contracts"]:
                    contract["source"] = result["source"]
                chains.append(result)
            if i % 20 == 0 or i == len(selected):
                print(f"Chains checked: {i}/{len(selected)}; valid dated chains: {len(chains)}.", flush=True)
    if not chains:
        atomic_json(Path("output") / "coverage.json", collector.coverage)
        raise RuntimeError("No verified same-session equity-option chains; no normal report can be sent")
    history = {d:read_json(state_dir / "chains" / f"{d}.json").get("symbols", {}) for d in prior_sessions}
    candidates, flagged_names = evaluate_chains(chains, occ_stats, history, prior_sessions, names, config)

    # Resolve separate call/put baseline only for the final research candidates.
    side_jobs = [(c["symbol"], c["underlying_occ"].get("raw_symbol", c["symbol"]), day)
                 for c in candidates for day in sessions]
    side_results = {}

    def load_sides(job):
        symbol, raw, day = job
        path = state_dir / "sides" / f"{day}-{symbol}.json"
        cached = read_json(path)
        if cached.get("schema") == 1 and cached.get("session") == day and cached.get("symbol") == raw:
            with collector.lock:
                collector.coverage.append({**cached["source"], "status":"cached", "cache_checked_at":iso()})
            return (symbol, day), cached
        result = collector.fetch(f"OCC call/put {symbol}", occ_sides_url(raw, day),
                                 lambda d:parse_occ_sides(d.body, raw, day), session=day)
        if result:
            result["schema"] = 1
            atomic_json(path, result)
        return (symbol, day), result

    if candidates:
        print(f"Verifying separate call/put history and event context for {len(candidates)} candidates.", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for key, value in pool.map(load_sides, side_jobs):
            side_results[key] = value
    for candidate in candidates:
        symbol = candidate["symbol"]
        current = side_results.get((symbol, session))
        candidate["occ_call_put"] = current
        candidate["call_relative_volume_5_sessions"] = None
        candidate["put_relative_volume_5_sessions"] = None
        for side in ("call", "put"):
            values = [(side_results.get((symbol, d)) or {}).get(side + "_account_volume") for d in prior_sessions]
            avg = baseline(values)
            candidate[side + "_baseline_sessions_available"] = sum(v is not None for v in values)
            candidate[side + "_relative_volume_5_sessions"] = ratio((current or {}).get(side + "_account_volume"), avg)
        candidate["activity_bias"] = ("call-heavy" if candidate["call_volume"] > 2*candidate["put_volume"] else
                                      "put-heavy" if candidate["put_volume"] > 2*candidate["call_volume"] else "mixed")

    event_dates = [(date.fromisoformat(session) + timedelta(days=i)).isoformat()
                   for i in range(config["event_calendar_days"] + 1)]

    def get_calendar(day):
        return collector.fetch("Nasdaq reported earnings calendar", "https://api.nasdaq.com/api/calendar/earnings?date=" + day,
                               lambda d:calendar_rows(d, day), session=day)

    events = []
    if candidates:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for value in pool.map(get_calendar, event_dates):
                if value:
                    events.append(value)
    for candidate in candidates:
        for calendar in events:
            for row in calendar["rows"]:
                if alias.get(row.get("symbol")) == candidate["symbol"]:
                    candidate["events"].append({"kind":"earnings", "date":calendar["date"],
                        "time":row.get("time"), "evidence":"Nasdaq calendar listing; issuer confirmation not obtained",
                        "source":calendar["source"], "source_url":"https://www.nasdaq.com/market-activity/earnings?date=" + calendar["date"]})
        symbol = candidate["symbol"]
        dividends = collector.fetch(f"Nasdaq dividend calendar {symbol}",
            "https://api.nasdaq.com/api/quote/" + quote(symbol, safe="") + "/dividends?assetclass=stocks",
            lambda d:d.json().get("data") or {})
        if dividends and dividends.get("exDividendDate"):
            try:
                ex_date = datetime.strptime(dividends["exDividendDate"], "%m/%d/%Y").date().isoformat()
                candidate["last_reported_ex_dividend_date"] = ex_date
                candidate["dividend_source"] = dividends["source"]
                if ex_date >= session:
                    candidate["ex_dividend_date"] = ex_date
            except ValueError:
                pass

    stats = {"listed_eligible_names":len(names), "occ_names_screened":len(occ_stats),
        "occ_exchange_root_records":occ.get(session, {}).get("records", 0),
        "cboe_discovery_names":len(discovery), "cboe_discovery_contract_rows":(discovery_raw or {}).get("records", 0),
        "discovered_eligible_names":len(set(occ_stats) | set(discovery)),
        "chains_attempted":len(selected), "chains_verified":len(chains), "chains_unavailable":len(selected)-len(chains),
        "chain_contract_rows_checked":sum(c["source_rows"] for c in chains),
        "eligible_contracts_checked":sum(c["eligible_rows"] for c in chains),
        "positive_volume_contracts":sum(len(c["contracts"]) for c in chains),
        "flagged_underlyings":flagged_names, "candidates":len(candidates),
        "discovered_names_not_chain_reviewed":len((set(occ_stats)|set(discovery))-set(selected)),
        "sources_unavailable":sum(x["status"] == "unavailable" for x in collector.coverage)}
    report = {"schema":1, "generated_at":iso(now), "session":session, "timezone":config["timezone"],
        "language":config["language"], "smoke":smoke, "synthetic":False, "evidence_level":"chain-level activity",
        "scope":"US exchange-listed common stocks/ADRs; bounded chain investigation after broad public discovery",
        "watchlist":supplied, "prior_sessions":prior_sessions, "stats":stats, "candidates":candidates,
        "coverage":collector.coverage, "chain_symbols":selected,
        "chain_audit":[{k:v for k,v in c.items() if k not in ("contracts", "history")} for c in chains],
        "history_sources":{day:value["source"] for day,value in occ.items()},
        "thresholds":{k:config[k] for k in ("minimum_contract_volume", "volume_oi_flag", "relative_volume_flag", "max_dte")},
        "limitations":["No execution tape, aggressor side, opening/closing classification or linked-leg identification.",
            "Public Cboe snapshots do not document OI as-of date, contract multiplier, deliverable or exact quote delay.",
            "V/OI snapshot flags are descriptive; OI=0 gives an undefined ratio. No paid-premium or dollar-flow claim.",
            "OCC account-volume sums and chain contract counts are different units and are never added together.",
            "Cboe C1 discovery covers one exchange. Chain exchange completeness is not enumerated by the response.",
            "Earnings/dividend calendar entries are provider reports, not issuer-confirmed catalysts. Missing fields remain unknown.",
            "Five-session contract history builds as daily snapshots accumulate; missing history is never replaced with zero.",
            "Repeated trading in one contract can inflate volume; call activity is not necessarily bullish and put activity is not necessarily bearish."]}
    if not smoke:
        atomic_json(state_dir / "chains" / f"{session}.json", {"schema":1, "session":session,
                    "symbols":{c["symbol"]:c["history"] for c in chains}})
        cutoff = (date.fromisoformat(session) - timedelta(days=20)).isoformat()
        for folder in ("occ", "chains", "sides"):
            for path in (state_dir / folder).glob("*.json"):
                if path.name[:10] < cutoff:
                    path.unlink()
    print(json.dumps(stats, sort_keys=True), flush=True)
    return report
