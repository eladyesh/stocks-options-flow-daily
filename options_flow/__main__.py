from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re

from .core import UTC, atomic_json, completed_sessions, read_json
from .pipeline import collect
from .report import save
from .telegram import Telegram, deliver


def main():
    parser = argparse.ArgumentParser(description="Free-source US equity options activity research")
    parser.add_argument("command", choices=["run", "send", "verify-telegram"])
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--output", default="output")
    parser.add_argument("--state", default="state")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--prepare-delivery", action="store_true")
    parser.add_argument("--symbols", help="Optional comma-separated eligible stock symbols; otherwise broad discovery")
    args = parser.parse_args()
    config = read_json(args.config)
    if not 1 <= config.get("max_candidates", 0) <= 10 or config.get("baseline_sessions") != 5:
        raise ValueError("At most ten candidates and five prior sessions are required")
    state_dir, output = Path(args.state), Path(args.output)
    if args.command in ("verify-telegram", "send"):
        bot = Telegram(os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", ""))
        if args.command == "verify-telegram":
            bot.verify()
            print("Configured private Telegram destination verified; no message sent.")
        else:
            report = read_json(output / "report.json")
            sent = deliver(report, state_dir / "delivery.json", bot)
            print(f"Telegram confirmed {sent} new report messages.")
        return
    if args.smoke and args.prepare_delivery:
        raise ValueError("Smoke checks cannot prepare Telegram delivery")
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
    if symbols and any(not re.fullmatch(r"[A-Z]{1,6}(?:[.-][A-Z])?", s) for s in symbols):
        raise ValueError("Invalid stock symbol")
    session = completed_sessions()[-1]
    pending = state_dir / "pending_report.json"
    report = read_json(pending)
    if args.prepare_delivery and report.get("session") == session:
        save(report, output)
        print("Reusing this session's report and receipts; completed deliveries will not be sent again.")
        return
    report = collect(config, state_dir, smoke=args.smoke, symbols=symbols)
    save(report, output)
    if args.prepare_delivery:
        atomic_json(pending, report)
    print(f"Report ready: {len(report['candidates'])} candidates for {session}; delivery is a separate command.")


if __name__ == "__main__":
    main()
