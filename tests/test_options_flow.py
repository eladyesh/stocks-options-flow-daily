from copy import deepcopy
from datetime import datetime, timedelta
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from options_flow.analysis import baseline, choose_symbols, evaluate_chains
from options_flow.core import UTC, completed_sessions, count, number, ratio
from options_flow.http import Download, validate_url
from options_flow.pipeline import calendar_rows
from options_flow.report import markdown, sections
from options_flow.sources import aliases, parse_chain, parse_directory, parse_occ_xml, parse_occ_sides, parse_cboe_symbols
from options_flow.telegram import Telegram, TelegramRejected, deliver, messages, units

SESSION = "2026-10-16"
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
CONFIG = json.loads((Path(__file__).parents[1]/"config.json").read_text())


def chain_data():
    return {"timestamp":"2026-10-17 03:30:00", "data":{"symbol":"ACME", "security_type":"stock",
        "current_price":102, "last_trade_time":"2026-10-16T15:59:59", "options":[
        {"option":"ACME261120C00105000", "volume":2000, "open_interest":100,
         "bid":2, "ask":2.2, "iv":.4, "last_trade_price":2.1, "last_trade_time":"2026-10-16T15:50:00"},
        {"option":"ACME261120P00095000", "volume":800, "open_interest":200,
         "bid":1, "ask":1.2, "iv":.3, "last_trade_price":1.1, "last_trade_time":"2026-10-16T15:45:00"}]}}


def example_report():
    chain = parse_chain(chain_data(), "ACME", SESSION)
    chain["source"] = {"url":"https://example.com/fixture", "retrieved_at":NOW.isoformat()}
    prior = ["2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"]
    candidates, _ = evaluate_chains([chain], {"ACME":{"relative":4}}, {}, prior, {"ACME":"Acme synthetic fixture"}, CONFIG)
    candidate = candidates[0]
    candidate.update(activity_bias="call-heavy", call_relative_volume_5_sessions=4,
                     put_relative_volume_5_sessions=None)
    stats = dict(discovered_eligible_names=100, chains_verified=1, chains_attempted=1,
        eligible_contracts_checked=2, discovered_names_not_chain_reviewed=99, chains_unavailable=0, sources_unavailable=0)
    return dict(session=SESSION, generated_at=NOW.isoformat(), smoke=False, synthetic=False,
        evidence_level="chain-level activity", prior_sessions=prior, stats=stats,
        candidates=candidates, thresholds={"max_dte":365}, coverage=[])


class NumericAndCalendarTests(unittest.TestCase):
    def test_missing_nonfinite_negative_and_fractional_counts_remain_unknown(self):
        for value in [None, "--", "NaN", "Infinity", -1, True]:
            self.assertIsNone(number(value))
        self.assertIsNone(count(1.5))
        self.assertEqual(count("1,500"), 1500)

    def test_zero_oi_has_no_infinite_ratio(self):
        self.assertIsNone(ratio(1000,0))
        self.assertIsNone(ratio(None,1))
        self.assertEqual(ratio(0,10),0)

    def test_missing_baseline_is_not_zero(self):
        self.assertIsNone(baseline([1,2,None,4,5]))
        self.assertIsNone(baseline([1,2,3]))
        self.assertEqual(baseline([0,0,0,0,0]),0)

    def test_weekend_uses_friday(self):
        self.assertEqual(completed_sessions(NOW)[-1],SESSION)

    def test_holiday_and_early_close(self):
        thanksgiving = datetime(2026,11,26,20,tzinfo=UTC)
        self.assertEqual(completed_sessions(thanksgiving)[-1],"2026-11-25")
        self.assertEqual(completed_sessions(datetime(2026,11,27,18,30,tzinfo=UTC))[-1],"2026-11-27")

    def test_delay_window_does_not_treat_live_session_as_completed(self):
        self.assertEqual(completed_sessions(datetime(2026,10,16,20,10,tzinfo=UTC))[-1],"2026-10-15")
        self.assertEqual(completed_sessions(datetime(2026,10,16,20,21,tzinfo=UTC))[-1],SESSION)


class SourceTests(unittest.TestCase):
    def test_listing_filters_etfs_spacs_preferreds_warrants(self):
        data = "Symbol|Security Name|Test Issue|ETF\nACME|Acme Common Stock|N|N\nETF|ETF Common Shares|N|Y\nSPA|Some Acquisition Ordinary Shares|N|N\nPRF|Acme Preferred Stock|N|N\nWT|Common Stock Warrants|N|N\nADR|Issuer American Depositary Shares|N|N\n"
        self.assertEqual(set(parse_directory(data)),{"ACME","ADR"})

    def test_ambiguous_symbol_alias_does_not_guess(self):
        mapping = aliases({"BRK.B":"Berkshire B", "BRKB":"Unrelated"})
        self.assertNotIn("BRKB",mapping)
        self.assertEqual(mapping["BRK.B"],"BRK.B")

    def test_occ_sums_accounts_and_keeps_units(self):
        xml = b'<results><record><marketQuantity>30</marketQuantity><firmQuantity>10</firmQuantity><customerQuantity>40</customerQuantity><pkind>OSTK</pkind><underlying>ACME</underlying><symbol>ACME</symbol><exchangeId>C</exchangeId><actdate>2026-10-16</actdate></record></results>'
        result = parse_occ_xml(xml,SESSION)
        self.assertEqual(result["totals"]["ACME"],80)
        self.assertIn("not single-count",result["units"])
        with self.assertRaises(ValueError):parse_occ_xml(xml,"2026-10-15")
        with self.assertRaises(ValueError):parse_occ_xml(xml.replace(b'</results>',xml[9:]),SESSION)

    def test_empty_or_incomplete_occ_is_not_zero_volume(self):
        with self.assertRaises(ValueError):parse_occ_xml(b'<results/>',SESSION)
        with self.assertRaises(ValueError):parse_occ_xml(b'<!DOCTYPE fake><results/>',SESSION)

    def test_call_put_units_window_and_duplicate_validation(self):
        body = b'quantity,underlying,symbol,actype,porc,exchange,actdate\n100,ACME,ACME,C,C,CBOE,10/16/2026\n50,ACME,ACME,C,P,CBOE,10/16/2026\n'
        result = parse_occ_sides(body,"ACME",SESSION)
        self.assertEqual(result["put_call_ratio"],.5)
        with self.assertRaises(ValueError):parse_occ_sides(body,"OTHER",SESSION)
        with self.assertRaises(ValueError):parse_occ_sides(body+body.splitlines(keepends=True)[1],"ACME",SESSION)

    def test_cboe_discovery_is_dated_and_limited_to_c1(self):
        body = b'Symbol,Call/Put,Expiration,Strike Price,Volume\nACME,C,2026-11-20,100,500\n'
        download = Download(body,"https://example.com",NOW.isoformat(),"Fri, 16 Oct 2026 20:55:39 GMT")
        result = parse_cboe_symbols(download,SESSION)
        self.assertEqual(result["symbols"]["ACME"]["C"],500)
        self.assertIn("C1",result["scope"])
        with self.assertRaises(ValueError):parse_cboe_symbols(download,"2026-10-15")

    def test_chain_snapshot_never_becomes_trade_tape(self):
        result = parse_chain(chain_data(),"ACME",SESSION)
        call = result["contracts"][0]
        self.assertEqual(call["volume_oi_snapshot_ratio"],20)
        for key in ("multiplier","deliverable","activity_proxy_usd","observed_premium","oi_as_of","sweep","opening_or_closing"):
            self.assertIsNone(call[key])
        self.assertEqual(call["direction"],"direction-unknown")

    def test_adjusted_roots_and_stale_positive_volume_are_excluded(self):
        data = chain_data()
        adjusted = dict(data["data"]["options"][0],option="ACME1261120C00105000")
        stale = dict(data["data"]["options"][0],option="ACME261120C00110000",last_trade_time="2026-10-15T15:00:00")
        data["data"]["options"] += [adjusted,stale]
        result = parse_chain(data,"ACME",SESSION)
        self.assertEqual(result["call_volume"],2000)
        self.assertEqual(result["exclusions"]["adjusted_or_other_root"],1)
        self.assertEqual(result["exclusions"]["positive_volume_without_same_session_trade"],1)

    def test_wrong_underlying_and_stale_chain_fail(self):
        with self.assertRaises(ValueError):parse_chain(chain_data(),"WRONG",SESSION)
        with self.assertRaises(ValueError):parse_chain(chain_data(),"ACME","2026-10-15")

    def test_crossed_quotes_and_zero_oi_are_unknown_ratios(self):
        data = chain_data()
        data["data"]["options"][0].update(bid=4,ask=2,open_interest=0)
        result = parse_chain(data,"ACME",SESSION)["contracts"][0]
        self.assertIsNone(result["midpoint"])
        self.assertIsNone(result["relative_spread"])
        self.assertIsNone(result["volume_oi_snapshot_ratio"])

    def test_duplicate_contract_is_not_counted_twice(self):
        data = chain_data()
        data["data"]["options"].append(data["data"]["options"][0])
        with self.assertRaises(ValueError):parse_chain(data,"ACME",SESSION)

    def test_calendar_date_must_match_request(self):
        payload = {"data":{"asOf":"Mon, Oct 19, 2026","rows":[]}}
        download = Download(json.dumps(payload).encode(),"https://example.com",NOW.isoformat(),None)
        self.assertEqual(calendar_rows(download,"2026-10-19")["rows"],[])
        with self.assertRaises(ValueError):calendar_rows(download,"2026-10-20")

    def test_only_configured_public_https_destinations(self):
        for url in ["http://cdn.cboe.com/a", "https://localhost/a", "https://127.0.0.1", "https://user:secret@cdn.cboe.com/a", "https://cdn.cboe.com:8443/a"]:
            with self.subTest(url=url),self.assertRaises(ValueError):validate_url(url)
        validate_url("https://cdn.cboe.com/api/global/delayed_quotes/options/AAPL.json")


class AnalysisAndReportTests(unittest.TestCase):
    def test_discovery_uses_both_routes_with_a_bound(self):
        cfg = dict(CONFIG,max_chain_symbols=6)
        selected = choose_symbols({"OCC":{"current":100000,"relative":8}},
            {"CALL":{"C":10000,"P":100,"max_contract":5000},"PUT":{"C":100,"P":12000,"max_contract":6000}},cfg,SESSION)
        self.assertTrue({"OCC","CALL","PUT"} <= set(selected))
        self.assertLessEqual(len(selected),6)

    def test_first_run_has_no_invented_contract_baseline_or_prior_oi(self):
        report = example_report()
        contract = report["candidates"][0]["contracts"][0]
        self.assertIsNone(contract["relative_volume_5_sessions"])
        self.assertIsNone(contract["verified_volume_prior_oi_ratio"])
        self.assertEqual(contract["baseline_sessions_available"],0)

    def test_wide_quote_lowers_confidence_despite_unusual_volume(self):
        data = chain_data()
        data["data"]["options"][0].update(bid=.1,ask=5)
        chain = parse_chain(data,"ACME",SESSION);chain["source"] = {}
        rows,_ = evaluate_chains([chain],{"ACME":{"relative":10}}, {},[],{"ACME":"Acme"},CONFIG)
        self.assertEqual(rows[0]["confidence"],"Low")
        self.assertTrue(rows[0]["weak_quote"])

    def test_complete_same_contract_history_supports_relative_volume(self):
        chain = parse_chain(chain_data(),"ACME",SESSION)
        chain["source"] = {}
        prior = ["2026-10-09","2026-10-12","2026-10-13","2026-10-14","2026-10-15"]
        history = {day:{"ACME":{c["id"]:{"volume":100} for c in chain["contracts"]}} for day in prior}
        results,_ = evaluate_chains([chain],{},history,prior,{"ACME":"Acme"},CONFIG)
        self.assertEqual(results[0]["contracts"][0]["relative_volume_5_sessions"],20)
        self.assertEqual(results[0]["direction"],"direction-unknown")

    def test_hebrew_report_contains_limits_dates_and_sources(self):
        report = example_report()
        text = "\n".join(sections(report))
        self.assertIn(SESSION,text)
        self.assertIn("כיוון: לא ידוע",text)
        self.assertIn("תאריך חסר",text)
        self.assertIn("לא חושב סכום כסף שנכנס",text)
        self.assertIn("https://www.cboe.com/",text)
        self.assertIn("|",markdown(report))

    def test_message_utf16_size_and_numbering(self):
        report = example_report()
        report["candidates"] *= 10
        chunks = messages(report)
        self.assertGreater(len(chunks),1)
        self.assertTrue(all(units(x)<=4096 for x in chunks))
        self.assertTrue(chunks[-1].startswith(f"[{len(chunks)}/{len(chunks)}]"))


class FakeBot:
    chat_id = "12345"
    destination = "fixture-only-destination"
    def __init__(self, error=None):
        self.calls,self.error = [],error
    def verify(self):pass
    def call(self, method, payload):
        self.calls.append(payload)
        if self.error:
            raise self.error
        return {"message_id":len(self.calls),"chat":{"id":12345,"type":"private"}}


class TelegramTests(unittest.TestCase):
    def test_own_bot_id_is_rejected(self):
        bot = Telegram("999:fixture-token","999")
        with patch.object(bot,"call",return_value={"id":999}) as call:
            with self.assertRaises(ValueError):bot.verify()
        call.assert_called_once_with("getMe",{})

    def test_known_rejection_is_distinct_and_error_body_is_redacted(self):
        bot = Telegram("999:fixture-token","12345")
        for body, kind in [({"ok":False,"error_code":403,"description":"Forbidden: bot was blocked by the user"},TelegramRejected),
                           ({"ok":False,"error_code":403,"description":f"secret {bot.token} {bot.chat_id}"},TelegramRejected),
                           ({"arbitrary":"response"},RuntimeError)]:
            error = HTTPError("redacted",403,"Forbidden",{},BytesIO(json.dumps(body).encode()))
            with patch("options_flow.telegram.build_opener") as opener:
                opener.return_value.open.side_effect=error
                with self.assertRaises(kind) as raised:bot.call("sendMessage",{})
                self.assertNotIn(bot.token,str(raised.exception))
                self.assertNotIn(bot.chat_id,str(raised.exception))

    @patch("options_flow.telegram.time.sleep")
    def test_success_is_not_sent_twice_even_after_weekend(self,_):
        with tempfile.TemporaryDirectory() as d:
            path,bot=Path(d)/"journal.json",FakeBot()
            report=example_report()
            sent=deliver(report,path,bot,NOW)
            self.assertGreater(sent,0)
            self.assertEqual(deliver(report,path,bot,NOW+timedelta(days=1)),0)
            self.assertEqual(len(bot.calls),sent)

    def test_ambiguous_send_stays_locked(self):
        with tempfile.TemporaryDirectory() as d:
            path,bot=Path(d)/"journal.json",FakeBot(TimeoutError("uncertain"))
            for _ in range(2):
                with self.assertRaises((TimeoutError,RuntimeError)):deliver(example_report(),path,bot,NOW)
            self.assertEqual(len(bot.calls),1)
            self.assertTrue(json.loads(path.read_text())[SESSION]["uncertain"])

    @patch("options_flow.telegram.time.sleep")
    def test_explicit_rejection_can_retry_without_unsafe_reset(self,_):
        with tempfile.TemporaryDirectory() as d:
            path,bot=Path(d)/"journal.json",FakeBot(TelegramRejected("HTTP 403"))
            with self.assertRaises(TelegramRejected):deliver(example_report(),path,bot,NOW)
            self.assertFalse(json.loads(path.read_text())[SESSION]["uncertain"])
            bot.error=None
            self.assertGreater(deliver(example_report(),path,bot,NOW),0)

    def test_wrong_receipt_stays_locked(self):
        bot=FakeBot()
        with tempfile.TemporaryDirectory() as d,patch.object(bot,"call",return_value={"message_id":1,"chat":{"id":999,"type":"private"}}):
            path=Path(d)/"journal.json"
            with self.assertRaises(RuntimeError):deliver(example_report(),path,bot,NOW)
            self.assertTrue(json.loads(path.read_text())[SESSION]["uncertain"])

    def test_stale_synthetic_smoke_wrong_session_and_changed_destination_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            for change in [{"generated_at":(NOW-timedelta(hours=7)).isoformat()},
                           {"generated_at":(NOW+timedelta(minutes=1)).isoformat()},
                           {"synthetic":True},{"smoke":True},{"session":"2026-10-15"}]:
                report=example_report();report.update(change)
                with self.subTest(change=change),self.assertRaises(ValueError):deliver(report,Path(d)/"journal.json",FakeBot(),NOW)

    @patch("options_flow.telegram.time.sleep")
    def test_changed_report_or_recipient_cannot_redeliver(self,_):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"journal.json"
            report=example_report();deliver(report,path,FakeBot(),NOW)
            other=FakeBot();other.destination="different-destination"
            with self.assertRaises(ValueError):deliver(report,path,other,NOW)
            modified=deepcopy(report);modified["candidates"][0]["company"]="Changed report"
            with self.assertRaises(ValueError):deliver(modified,path,FakeBot(),NOW)

    @patch("options_flow.telegram.time.sleep")
    def test_partial_explicit_rejection_resumes_after_confirmed_message(self,_):
        class PartialBot(FakeBot):
            def call(self,method,payload):
                self.calls.append(payload)
                if len(self.calls)==2:raise TelegramRejected("known rejection")
                return {"message_id":len(self.calls),"chat":{"id":12345,"type":"private"}}
        report=example_report();report["candidates"]*=5
        with tempfile.TemporaryDirectory() as d:
            path,bot=Path(d)/"journal.json",PartialBot()
            with self.assertRaises(TelegramRejected):deliver(report,path,bot,NOW)
            self.assertEqual(json.loads(path.read_text())[SESSION]["cursor"],1)
            self.assertGreater(deliver(report,path,bot,NOW),0)
            self.assertEqual(sum(x["text"]==messages(report)[0] for x in bot.calls),1)


if __name__ == "__main__":
    unittest.main()
