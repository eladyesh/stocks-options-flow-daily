"""Private destination validation, bounded messages and conservative delivery receipts."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import hmac
import json
import re
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .core import UTC, atomic_json, completed_sessions, digest, iso, read_json
from .report import sections


class TelegramRejected(RuntimeError):
    """Telegram explicitly returned a structured rejection, so this send was not accepted."""


def hint(data):
    descriptions = {
        "Forbidden: bot was blocked by the user":"Unblock the bot and press Start",
        "Forbidden: bot can't initiate conversation with a user":"Open the bot and press Start",
        "Forbidden: bot can't send messages to bots":"Use your message.chat.id from getUpdates, not a bot ID",
        "Bad Request: chat not found":"Start the bot and check TELEGRAM_CHAT_ID",
        "Unauthorized":"Check TELEGRAM_BOT_TOKEN",
    }
    description = data.get("description") if isinstance(data, dict) else None
    return descriptions.get(description, "credentials omitted") if isinstance(description, str) else "credentials omitted"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Telegram redirects are refused")


class Telegram:
    def __init__(self, token, chat_id):
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token or ""):
            raise ValueError("TELEGRAM_BOT_TOKEN is missing or malformed")
        if not re.fullmatch(r"[1-9]\d{0,15}", chat_id or ""):
            raise ValueError("TELEGRAM_CHAT_ID must be a positive private-chat ID")
        self.token, self.chat_id = token, chat_id
        self.destination = hmac.new(token.encode(), chat_id.encode(), hashlib.sha256).hexdigest()

    def call(self, method, payload):
        request = Request(f"https://api.telegram.org/bot{self.token}/{method}",
            data=json.dumps(payload).encode(), headers={"Content-Type":"application/json"}, method="POST")
        try:
            with build_opener(NoRedirect()).open(request, timeout=30) as response:
                data = json.load(response)
        except HTTPError as exc:
            try:
                data = json.loads(exc.read(4096))
            except Exception:
                data = None
            if (isinstance(data, dict) and data.get("ok") is False
                    and data.get("error_code") == exc.code and 400 <= exc.code < 500):
                raise TelegramRejected(f"Telegram rejected {method} (HTTP {exc.code}); {hint(data)}") from None
            raise RuntimeError(f"Telegram {method} outcome uncertain (HTTP {exc.code}); review before retrying") from None
        except Exception:
            raise RuntimeError(f"Telegram {method} outcome uncertain; review before retrying") from None
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected Telegram response; outcome uncertain")
        if data.get("ok") is False:
            raise TelegramRejected(f"Telegram rejected {method}; {hint(data)}")
        if data.get("ok") is not True or "result" not in data:
            raise RuntimeError("Telegram response did not confirm acceptance")
        return data["result"]

    def verify(self):
        identity = self.call("getMe", {})
        if str(identity.get("id")) == self.chat_id:
            raise ValueError("TELEGRAM_CHAT_ID is this bot's own ID; use message.chat.id from getUpdates")
        chat = self.call("getChat", {"chat_id":self.chat_id})
        if chat.get("type") != "private" or str(chat.get("id")) != self.chat_id:
            raise ValueError("Configured Telegram destination is not the expected private chat")


def units(value):
    return len(value.encode("utf-16-le")) // 2


def messages(report):
    chunks, current = [], ""
    for section in sections(report):
        if current and units(current + "\n\n" + section) <= 3500:
            current += "\n\n" + section
            continue
        if current:
            chunks.append(current)
        current = ""
        for line in section.splitlines():
            while units(line) > 3500:
                cut = min(len(line), 3500)
                while units(line[:cut]) > 3500:
                    cut -= 1
                if current:
                    chunks.append(current.rstrip()); current = ""
                chunks.append(line[:cut]); line = line[cut:]
            if current and units(current + line + "\n") > 3500:
                chunks.append(current.rstrip()); current = ""
            current += line + "\n"
        current = current.rstrip()
    if current:
        chunks.append(current)
    return [f"[{i}/{len(chunks)}]\n{value}" for i, value in enumerate(chunks, 1)]


def deliver(report, journal_path, bot, now=None):
    now = now or datetime.now(UTC)
    if report.get("smoke") or report.get("synthetic") or report.get("evidence_level") != "chain-level activity":
        raise ValueError("Only a verified, full live report is eligible for delivery")
    if report["session"] != completed_sessions(now)[-1]:
        raise ValueError("Report is not for the latest completed US session")
    chunks = messages(report)
    fingerprint = digest(json.dumps(chunks, ensure_ascii=False))
    state = read_json(journal_path)
    journal = state.setdefault(report["session"], {"cursor":0, "fingerprint":fingerprint, "destination":bot.destination})
    if journal["fingerprint"] != fingerprint or journal.get("destination") != bot.destination:
        raise ValueError("Session report or destination differs from the saved delivery; review required")
    if journal.get("uncertain"):
        raise RuntimeError("A prior send has an uncertain outcome; review the chat and journal before recovery")
    if journal.get("complete"):
        return 0
    generated = datetime.fromisoformat(report["generated_at"])
    if generated.tzinfo is None or not timedelta(0) <= now-generated <= timedelta(hours=6):
        raise ValueError("Report is stale or future-dated")
    bot.verify()
    sent = 0
    for index in range(journal["cursor"], len(chunks)):
        journal["uncertain"] = True
        atomic_json(journal_path, state)
        try:
            result = bot.call("sendMessage", {"chat_id":bot.chat_id, "text":chunks[index],
                              "link_preview_options":{"is_disabled":True}})
        except TelegramRejected:
            journal.update(uncertain=False, last_result="explicit rejection; no message accepted")
            atomic_json(journal_path, state)
            raise
        chat = result.get("chat", {})
        if not isinstance(result.get("message_id"), int) or chat.get("type") != "private" or str(chat.get("id")) != bot.chat_id:
            raise RuntimeError("Telegram receipt did not confirm the expected private destination")
        journal.update(cursor=index+1, uncertain=False, last_message_id=result["message_id"], last_result="accepted")
        atomic_json(journal_path, state)
        sent += 1
        time.sleep(1.1)
    journal.update(complete=True, completed_at=iso(now), messages=len(chunks))
    atomic_json(journal_path, state)
    return sent
