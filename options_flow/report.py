"""English reports with explicit boundaries between observations and inferences."""
from datetime import datetime
from pathlib import Path

from .core import NY, atomic_json


def fmt(value, digits=0):
    if value is None:
        return "unknown"
    return f"{value:,.{digits}f}"


def multiple(value):
    return "unavailable" if value is None else f"{value:.2f}×"


def percent(value):
    return "unknown" if value is None else f"{value * 100:.1f}%"


def sections(report):
    stats = report["stats"]
    intro = (f"Unusual Options Activity — {report['session']}\n"
        "Delayed chain data; buyer/seller identity and trade direction are unknown.\n"
        f"Broad discovery: {stats['discovered_eligible_names']:,} US stocks/ADRs; "
        f"verified {stats['chains_verified']}/{stats['chains_attempted']} chains and reviewed "
        f"{stats['eligible_contracts_checked']:,} contracts within 0–{report['thresholds']['max_dte']} days to expiration.\n"
        f"Five-session comparison: {report['prior_sessions'][0]} through {report['prior_sessions'][-1]}.\n"
        "V/OI uses the available OI snapshot. Its date is not supplied, so this does not confirm new positions. "
        "Executed premium, contract multiplier and deliverable are unverified; no dollar inflow is calculated.\n"
        f"{len(report['candidates'])} research candidates; bullish/bearish buying flow is not established.")
    if report.get("smoke"):
        intro = "Limited source check — not eligible for delivery\n" + intro
    output = [intro]
    today = datetime.fromisoformat(report["generated_at"]).astimezone(NY).date().isoformat()
    for index, candidate in enumerate(report["candidates"], 1):
        bias = {"call-heavy":"Call-heavy activity", "put-heavy":"Put-heavy activity", "mixed":"Mixed activity"}[candidate["activity_bias"]]
        lines = [f"{index}. {candidate['symbol']} — {candidate['company']}",
            f"{bias}; direction: unknown. Reported spot ${fmt(candidate['spot'],2)}.",
            f"Calls: {fmt(candidate['call_volume'])} | Puts: {fmt(candidate['put_volume'])} | Contract P/C: {fmt(candidate['put_call_ratio'],2)}",
            f"OCC activity vs. five-session average: total {multiple(candidate['underlying_occ'].get('relative'))}; "
            f"calls {multiple(candidate['call_relative_volume_5_sessions'])}; puts {multiple(candidate['put_relative_volume_5_sessions'])}."]
        for contract in candidate["contracts"]:
            expired = " (expiration has passed)" if contract["expiration"] < today else ""
            lines.extend([f"{'CALL' if contract['side']=='C' else 'PUT'} {contract['expiration']} ${fmt(contract['strike'],2)} — DTE {contract['dte']} on the observation date{expired}",
                f"Volume {fmt(contract['volume'])} | OI {fmt(contract['open_interest'])} (date unknown) | Snapshot V/OI {multiple(contract['volume_oi_snapshot_ratio'])}",
                f"Contract volume vs. five sessions: {multiple(contract['relative_volume_5_sessions'])} "
                f"({contract['baseline_sessions_available']}/5 observations); IV {percent(contract['iv'])}.",
                f"Bid/Ask ${fmt(contract['bid'],2)}/${fmt(contract['ask'],2)}; "
                f"spread {percent(contract['relative_spread'])}. "
                f"Last trade: {contract['last_trade_time']} ET; no execution-synchronized quote."])
        if candidate["events"]:
            lines.append("Nasdaq earnings calendar: " + ", ".join(e["date"] for e in candidate["events"]) + " — not issuer-confirmed.")
        else:
            lines.append("Verified earnings date: unknown; this does not rule out a nearby event.")
        if candidate.get("ex_dividend_date"):
            lines.append("Nasdaq ex-dividend date: " + candidate["ex_dividend_date"] + ".")
        alternatives = "Call selling, put hedging, spread legs or position rolls are possible."
        if any(c["dte"] <= 2 for c in candidate["contracts"]):
            alternatives += " Near-term expiration may explain closing or rolling activity."
        if candidate["events"]:
            alternatives += " Earnings may increase activity and implied volatility."
        lines.append("Alternative explanation: " + alternatives)
        evidence = ("Low — wide, zero-bid or invalid quote limits interpretation" if candidate.get("weak_quote") else
            "Medium — dated source and comparable volume baseline" if candidate["confidence"] == "Medium" else
            "Low — snapshot V/OI without a dated OI or sufficient historical baseline")
        lines.extend(["Evidence quality: " + evidence + "; buying and opening are unverified.", "Source: " + candidate["source_url"]])
        output.append("\n".join(lines))
    if not report["candidates"]:
        output.append("No candidates met the report thresholds in the reviewed chains. Unreviewed stocks may still have unusual activity.")
    output.append(f"Coverage limits: {stats['discovered_names_not_chain_reviewed']:,} discovered names were not reviewed at chain level; "
        f"{stats['chains_unavailable']} chains unavailable/stale and {stats['sources_unavailable']} source requests unsuccessful.\n"
        "Discovery sources: OCC account-volume data and Cboe C1, a single exchange. Historical ratios use the same source and units; these counts are not added to chain volume.\n"
        "Next verification: confirm contract terms, the OI as-of date and subsequent OI changes. A synchronized execution tape is needed to infer the initiating buyer/seller.\n"
        "Full source provenance and coverage are saved in this run's report artifacts.\n"
        "https://github.com/eladyesh/stocks-options-flow-daily/actions")
    return output


def markdown(report):
    blocks = sections(report)
    table = ["| Stock | Contract / DTE | Date / evidence level | Volume / OI / OI date | Premium / multiplier | Unusual activity | Direction / quality | Alternative explanation | Source |",
             "|---|---|---|---|---|---|---|---|---|"]
    for candidate in report["candidates"]:
        for contract in candidate["contracts"]:
            table.append(f"| {candidate['symbol']} | {contract['side']} {contract['expiration']} ${contract['strike']:g}; DTE {contract['dte']} "
                f"| {report['session']}; chain-level | {fmt(contract['volume'])} / {fmt(contract['open_interest'])} / unknown "
                f"| unknown / not supplied | Snapshot V/OI {multiple(contract['volume_oi_snapshot_ratio'])}; "
                f"contract volume vs. 5d {multiple(contract['relative_volume_5_sessions'])} | unknown / {candidate['confidence']} "
                f"| Possible hedge / spread / roll | [Cboe]({candidate['source_url']}) |")
    return "# Options Activity Report\n\n" + blocks[0] + "\n\n" + "\n".join(table) + "\n\n" + "\n\n---\n\n".join(blocks[1:]) + "\n"


def save(report, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "report.json", report)
    atomic_json(output / "coverage.json", report["coverage"])
    (output / "report.md").write_text(markdown(report))
    (output / "telegram.txt").write_text("\n\n".join(sections(report)) + "\n")
