from __future__ import annotations

from collections import defaultdict
import csv
from datetime import date, datetime
from email.utils import parsedate_to_datetime
import io
import re
from urllib.parse import urlencode, quote
import xml.etree.ElementTree as ET

from .core import NY, count, number, ratio

DIRECTORIES = ["https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
               "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"]
CBOE_SYMBOLS = "https://cdn-api.cboe.com/api/us/options/market-statistics/symbol-data/cone/cone-options-symbol-data.csv"
OCC_BASE = "https://marketdata.theocc.com/"


def occ_url(session):
    return OCC_BASE + "trade-volume-download?" + urlencode({"reportDate":session.replace("-", ""), "format":"xml"})


def occ_sides_url(symbol, session):
    return OCC_BASE + "volume-query?" + urlencode({"reportDate":session.replace("-", ""), "format":"csv",
        "volumeQueryType":"O", "symbolType":"U", "symbol":symbol, "reportType":"D",
        "accountType":"ALL", "productKind":"OSTK", "porc":"BOTH"})


def chain_url(symbol):
    return "https://cdn.cboe.com/api/global/delayed_quotes/options/" + quote(symbol, safe="") + ".json"


def public_chain_url(symbol):
    return "https://www.cboe.com/delayed_quotes/" + quote(symbol, safe="") + "/quote_table"


def parse_directory(text):
    names = {}
    for row in csv.DictReader(io.StringIO(text), delimiter="|"):
        symbol = row.get("Symbol") or row.get("ACT Symbol")
        name = row.get("Security Name", "")
        if (not symbol or not re.fullmatch(r"[A-Z]{1,6}(?:[.\-][A-Z])?", symbol)
                or row.get("ETF") != "N" or row.get("Test Issue") != "N"
                or not re.search(r"common|ordinary|depositary|depository|shares of beneficial interest", name, re.I)
                or re.search(r"warrants?|preferred|preference|acquisition|\bunits?\b|rights? to|\b(?:fund|funds|portfolio|ETF|ETN)\b", name, re.I)):
            continue
        names[symbol] = name
    if len(names) < 1:
        raise ValueError("No eligible common stocks/ADRs in listing directory")
    return names


def aliases(names):
    groups = defaultdict(set)
    for symbol in names:
        groups[symbol].add(symbol)
        groups[symbol.replace(".", "").replace("-", "")].add(symbol)
    return {key:next(iter(values)) for key, values in groups.items() if len(values) == 1}


def parse_occ_xml(body, session):
    if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
        raise ValueError("Unexpected XML declarations")
    root = ET.fromstring(body)
    totals, seen = defaultdict(int), set()
    rows = 0
    for record in root.findall("record"):
        row = {child.tag:(child.text or "").strip() for child in record}
        if row.get("actdate") != session:
            raise ValueError("OCC data date does not match the requested session")
        if row.get("pkind") != "OSTK":
            continue
        key = tuple(row.get(k) for k in ("underlying", "symbol", "exchangeId", "actdate", "pkind"))
        if key in seen:
            raise ValueError("Duplicate OCC exchange/root record")
        seen.add(key)
        values = [count(row.get(k)) for k in ("marketQuantity", "firmQuantity", "customerQuantity")]
        if not row.get("underlying") or any(v is None for v in values):
            raise ValueError("Missing OCC account-volume fields")
        totals[row["underlying"]] += sum(values)
        rows += 1
    if not rows:
        raise ValueError("OCC has no published equity-option records for this session")
    return {"session":session, "totals":dict(totals), "records":rows,
            "units":"OCC customer + firm + market-maker account-volume sum; not single-count contract volume"}


def parse_occ_sides(body, symbol, session):
    rows = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    if not {"quantity", "underlying", "symbol", "actype", "porc", "exchange", "actdate"} <= set(rows.fieldnames or []):
        raise ValueError("OCC call/put response is not the documented CSV")
    totals, seen = {"C":0, "P":0}, set()
    for row in rows:
        observed = datetime.strptime(row["actdate"], "%m/%d/%Y").date().isoformat()
        if observed != session or row["underlying"] != symbol or row["porc"] not in totals or row["actype"] not in ("C", "F", "M"):
            raise ValueError("OCC call/put observation identity mismatch")
        key = tuple(row[k] for k in ("symbol", "actype", "porc", "exchange", "actdate"))
        if key in seen:
            raise ValueError("Duplicate OCC call/put record")
        seen.add(key)
        value = count(row["quantity"])
        if value is None:
            raise ValueError("Missing OCC call/put quantity")
        totals[row["porc"]] += value
    if not seen:
        raise ValueError("No documented OCC call/put records; missing is not zero")
    return {"session":session, "symbol":symbol, "call_account_volume":totals["C"],
            "put_account_volume":totals["P"], "put_call_ratio":ratio(totals["P"], totals["C"]),
            "units":"OCC account-volume sum, same source/session/window for calls and puts"}


def parse_cboe_symbols(download, session):
    # This timestamp dates the public file, not a guaranteed exchange observation.
    # It is a discovery route only; every finalist is checked against a dated chain.
    if not download.modified or parsedate_to_datetime(download.modified).astimezone(NY).date().isoformat() != session:
        raise ValueError("Cboe discovery file is not updated in the requested session")
    rows = csv.DictReader(io.StringIO(download.body.decode("utf-8-sig")))
    if not {"Symbol", "Call/Put", "Volume", "Expiration", "Strike Price"} <= set(rows.fieldnames or []):
        raise ValueError("Unexpected Cboe symbol-data schema")
    groups = defaultdict(lambda:{"C":0, "P":0, "max_contract":0})
    n = 0
    for row in rows:
        side, volume = row["Call/Put"], count(row["Volume"])
        if side not in ("C", "P") or volume is None:
            raise ValueError("Incomplete Cboe discovery record")
        group = groups[row["Symbol"]]
        group[side] += volume
        group["max_contract"] = max(volume, group["max_contract"])
        n += 1
    if not n:
        raise ValueError("Empty Cboe discovery data")
    return {"symbols":dict(groups), "records":n, "scope":"Cboe C1 exchange only; discovery, not consolidated flow",
            "date_basis":"HTTP Last-Modified of the exchange's current-session file"}


CONTRACT = re.compile(r"^([A-Z0-9.\-]+?)(\d{6})([CP])(\d{8})$")


def parse_chain(data, symbol, session, max_dte=365):
    raw = data.get("data") or {}
    if raw.get("symbol") not in {symbol, symbol.replace(".", "").replace("-", "")} or raw.get("security_type") != "stock":
        raise ValueError("Chain is not the expected stock underlying")
    spot_time = str(raw.get("last_trade_time", ""))
    if spot_time[:10] != session:
        raise ValueError("Underlying observation is stale or belongs to a different session")
    spot = number(raw.get("current_price"))
    if not spot:
        raise ValueError("Missing underlying price")
    all_rows = raw.get("options")
    if not isinstance(all_rows, list) or not all_rows:
        raise ValueError("No option-chain records")
    contracts, history, seen = [], {}, set()
    exclusions = defaultdict(int)
    calls = puts = 0
    for row in all_rows:
        code = row.get("option", "")
        match = CONTRACT.fullmatch(code)
        if not match:
            exclusions["unrecognized_contract"] += 1
            continue
        root, expiry, side, strike = match.groups()
        if code in seen:
            raise ValueError("Duplicate option identifier in chain")
        seen.add(code)
        # Adjusted/numeric option roots need independent deliverable verification.
        if root != symbol.replace(".", "").replace("-", "") and root != symbol:
            exclusions["adjusted_or_other_root"] += 1
            continue
        expiration = datetime.strptime(expiry, "%y%m%d").date()
        dte = (expiration - date.fromisoformat(session)).days
        if not 0 <= dte <= max_dte:
            exclusions["outside_dte_window"] += 1
            continue
        volume, oi = count(row.get("volume")), count(row.get("open_interest"))
        if volume is None:
            exclusions["missing_volume"] += 1
            continue
        trade_time = str(row.get("last_trade_time", ""))
        if volume > 0 and trade_time[:10] != session:
            exclusions["positive_volume_without_same_session_trade"] += 1
            continue
        history[code] = {"volume":volume, "oi":oi, "oi_as_of":None}
        calls += volume if side == "C" else 0
        puts += volume if side == "P" else 0
        if not volume:
            continue
        bid, ask = number(row.get("bid")), number(row.get("ask"))
        valid_quote = bid is not None and ask is not None and ask >= bid and ask > 0
        midpoint = (bid + ask) / 2 if valid_quote else None
        spread = ratio(ask - bid, midpoint) if valid_quote else None
        contracts.append({"id":code, "underlying":symbol, "side":side,
            "expiration":expiration.isoformat(), "strike":int(strike)/1000, "dte":dte,
            "volume":volume, "open_interest":oi, "oi_as_of":None,
            "volume_oi_snapshot_ratio":ratio(volume, oi),
            "bid":bid, "ask":ask, "midpoint":midpoint, "relative_spread":spread,
            "iv":number(row.get("iv")) or None, "last_trade_price":number(row.get("last_trade_price")),
            "last_trade_time":trade_time, "last_trade_timezone":"America/New_York (dashboard ET)",
            "quote_timestamp":None, "spot":spot, "spot_timestamp":spot_time,
            "spot_timestamp_basis":"provider underlying last_trade_time, ET; not synchronized to option executions",
            "moneyness_strike_over_spot":int(strike)/1000/spot,
            "multiplier":None, "deliverable":None, "observed_premium":None, "activity_proxy_usd":None,
            "premium_note":"No execution tape or documented multiplier; no premium/proxy fabricated",
            "direction":"direction-unknown", "evidence_level":"chain-level activity",
            "sweep":None, "opening_or_closing":None, "linked_legs":None,
            "currency":"USD", "units":"contracts; quoted price per option unit",
            "session":session, "source_url":public_chain_url(symbol)})
    if not contracts:
        raise ValueError("No positive-volume, same-session eligible option contracts")
    return {"symbol":symbol, "session":session, "contracts":contracts, "history":history,
            "call_volume":calls, "put_volume":puts, "put_call_ratio":ratio(puts, calls),
            "spot":spot, "spot_timestamp":spot_time, "source_rows":len(all_rows),
            "eligible_rows":len(history), "exclusions":dict(exclusions),
            "refresh_timestamp_raw":data.get("timestamp"), "refresh_timezone":"not documented by JSON feed",
            "delay":"Delayed public chain; exact delay not provided by this response",
            "exchange_coverage":"Provider public chain; exchange completeness not enumerated",
            "oi_date_status":"OI as-of date not provided; snapshot V/OI is descriptive only",
            "source_url":public_chain_url(symbol)}
