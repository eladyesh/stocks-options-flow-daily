from __future__ import annotations

import math
from statistics import mean

from .core import ratio


def baseline(values, expected=5):
    if len(values) != expected or any(value is None for value in values):
        return None
    return mean(values)


def relative_volume(current, previous):
    return ratio(current, baseline(previous))


def choose_symbols(occ_stats, discovery, config, session, watchlist=None):
    """Interleave independent discovery routes; retain a small rotating broad sample."""
    limit = config["max_chain_symbols"]
    if watchlist:
        return list(dict.fromkeys(watchlist))[:limit]
    occ_order = sorted((s for s, x in occ_stats.items() if x["current"] >= config["minimum_occ_account_volume"]),
                       key=lambda s:(occ_stats[s]["relative"] or 0, occ_stats[s]["current"]), reverse=True)
    calls = sorted(discovery, key=lambda s:discovery[s]["C"], reverse=True)
    puts = sorted(discovery, key=lambda s:discovery[s]["P"], reverse=True)
    concentration = sorted(discovery, key=lambda s:discovery[s]["max_contract"], reverse=True)
    from .core import digest
    rotating = sorted(set(occ_stats) | set(discovery), key=lambda s:digest(session + s))
    selected = []
    for i in range(max(map(len, [occ_order, calls, puts, concentration]), default=0)):
        for route in (occ_order, calls, puts, concentration):
            if i < len(route) and route[i] not in selected:
                selected.append(route[i])
                if len(selected) >= max(1, limit - min(10, limit // 5)):
                    break
        if len(selected) >= max(1, limit - min(10, limit // 5)):
            break
    for symbol in rotating:
        if len(selected) >= limit:
            break
        if symbol not in selected:
            selected.append(symbol)
    return selected[:limit]


def evaluate_chains(chains, occ_stats, history, prior_sessions, names, config):
    candidates = []
    for chain in chains:
        symbol = chain["symbol"]
        occ = occ_stats.get(symbol, {})
        for contract in chain["contracts"]:
            prior = [history.get(d, {}).get(symbol, {}).get(contract["id"], {}).get("volume") for d in prior_sessions]
            contract["baseline_volume"] = baseline(prior)
            contract["baseline_sessions_available"] = sum(v is not None for v in prior)
            contract["relative_volume_5_sessions"] = ratio(contract["volume"], contract["baseline_volume"])
            # OI snapshots lack a documented OI date. They never establish a verified prior-OI signal.
            contract["verified_volume_prior_oi_ratio"] = None
            contract["next_session_oi_change"] = None
            contract["snapshot_oi_flag"] = (contract["volume_oi_snapshot_ratio"] or 0) > config["volume_oi_flag"]
            historical_flag = (contract["relative_volume_5_sessions"] or 0) > config["relative_volume_flag"]
            underlying_flag = (occ.get("relative") or 0) > config["relative_volume_flag"]
            reasons = []
            if historical_flag:
                reasons.append("same-contract volume exceeds its five-session baseline")
            if underlying_flag:
                reasons.append("underlying OCC account-volume exceeds its five-session baseline")
            if contract["snapshot_oi_flag"]:
                reasons.append("snapshot V/OI above threshold; OI date unknown, descriptive flag only")
            contract["flags"] = reasons
            contract["priority_key"] = [int(historical_flag)*4 + int(underlying_flag)*3 + int(contract["snapshot_oi_flag"]),
                                        min(contract["relative_volume_5_sessions"] or occ.get("relative") or 0, 50),
                                        math.log1p(contract["volume"])]
        relevant = [x for x in chain["contracts"] if x["volume"] >= config["minimum_contract_volume"] and x["flags"]]
        if not relevant:
            continue
        relevant.sort(key=lambda x:x["priority_key"], reverse=True)
        lead = relevant[0]
        opposite = next((x for x in relevant if x["side"] != lead["side"]), None)
        contracts = [lead] + ([opposite] if opposite else [])
        confidence = "Medium" if lead["priority_key"][0] >= 3 else "Low"
        quote_weak = lead["relative_spread"] is None or lead["relative_spread"] > .5 or not lead["bid"]
        if quote_weak:
            confidence = "Low"
        candidates.append({"symbol":symbol, "company":names[symbol], "contracts":contracts,
            "call_volume":chain["call_volume"], "put_volume":chain["put_volume"],
            "put_call_ratio":chain["put_call_ratio"], "aggregate_scope":f"eligible standard-root chain contracts, DTE 0–{config['max_dte']}, same session",
            "underlying_occ":occ, "spot":chain["spot"], "spot_timestamp":chain["spot_timestamp"],
            "available_expirations":sorted({x["expiration"] for x in chain["contracts"]}),
            "direction":"direction-unknown", "evidence_level":"chain-level activity",
            "confidence":confidence,
            "weak_quote":quote_weak,
            "confidence_reason":("Wide/zero-bid/unusable quote lowers confidence in price and activity interpretation; counts remain dated observations" if quote_weak else
                "Dated primary-source chain and comparable underlying/contract history; no execution tape and OI date unknown" if confidence == "Medium" else
                "Dated chain but unusualness rests on an undated OI snapshot; historical verification missing"),
            "alternative":"Calls may be sold or part of a spread/roll; puts may hedge a long holding. Expiration, event IV and closing trades can inflate volume.",
            "next_verification":"Check deliverable/multiplier, documented OI dates and next-session OI; a synchronized execution tape is required for aggressor inference.",
            "source_url":chain["source_url"], "source":chain["source"],
            "refresh_timestamp_raw":chain["refresh_timestamp_raw"], "delay":chain["delay"],
            "events":[], "ex_dividend_date":None,
            "priority_key":lead["priority_key"]})
    candidates.sort(key=lambda x:x["priority_key"], reverse=True)
    return candidates[:config["max_candidates"]], len(candidates)
