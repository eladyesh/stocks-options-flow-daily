"""Human-readable Hebrew report with explicit observation and inference boundaries."""
from datetime import datetime
from pathlib import Path

from .core import NY, atomic_json


def fmt(value, digits=0):
    if value is None:
        return "לא ידוע"
    return f"{value:,.{digits}f}"


def multiple(value):
    return "לא זמין" if value is None else f"{value:.2f}×"


def sections(report):
    stats = report["stats"]
    intro = (f"פעילות אופציות חריגה — {report['session']}\n"
        "נתוני שרשרת מושהים; זהות הקונה/המוכר וכיוון העסקה אינם ידועים.\n"
        f"סינון רחב: {stats['discovered_eligible_names']:,} מניות/ADR בארה״ב; "
        f"נבדקו {stats['chains_verified']}/{stats['chains_attempted']} שרשראות ו־{stats['eligible_contracts_checked']:,} חוזים בטווח 0–{report['thresholds']['max_dte']} ימים לפקיעה.\n"
        f"השוואה לחמשת ימי המסחר: {report['prior_sessions'][0]} עד {report['prior_sessions'][-1]}.\n"
        "V/OI הוא יחס לצילום ה־OI הזמין; תאריך ה־OI לא נמסר ולכן אינו אישור לפתיחת פוזיציות. "
        "אין נתוני פרמיה ששולמה, מכפיל או נכס למסירה מאומתים; לא חושב סכום כסף שנכנס.\n"
        f"{len(report['candidates'])} מועמדים לבדיקה; אין כאן זיהוי מאומת של זרימת קניות שורית/דובית.")
    if report.get("smoke"):
        intro = "בדיקת מקורות מצומצמת — אינה מיועדת לשליחה\n" + intro
    output = [intro]
    today = datetime.fromisoformat(report["generated_at"]).astimezone(NY).date().isoformat()
    for index, candidate in enumerate(report["candidates"], 1):
        bias = {"call-heavy":"ריכוז בקולים", "put-heavy":"ריכוז בפוטים", "mixed":"פעילות מעורבת"}[candidate["activity_bias"]]
        lines = [f"{index}. {candidate['symbol']} — {candidate['company']}",
            f"{bias}; כיוון: לא ידוע. Spot מדווח ${fmt(candidate['spot'],2)}.",
            f"קולים: {fmt(candidate['call_volume'])} | פוטים: {fmt(candidate['put_volume'])} | P/C חוזים: {fmt(candidate['put_call_ratio'],2)}",
            f"מחזור OCC מול ממוצע 5 ימים: כולל {multiple(candidate['underlying_occ'].get('relative'))}; "
            f"קולים {multiple(candidate['call_relative_volume_5_sessions'])}; פוטים {multiple(candidate['put_relative_volume_5_sessions'])}."]
        for contract in candidate["contracts"]:
            expired = " (הפקיעה כבר חלפה)" if contract["expiration"] < today else ""
            lines.extend([f"{'CALL' if contract['side']=='C' else 'PUT'} {contract['expiration']} ${fmt(contract['strike'],2)} — DTE {contract['dte']} ביום הנתונים{expired}",
                f"מחזור {fmt(contract['volume'])} | OI {fmt(contract['open_interest'])} (תאריך חסר) | V/OI צילום {multiple(contract['volume_oi_snapshot_ratio'])}",
                f"מחזור חוזה מול 5 ימים: {multiple(contract['relative_volume_5_sessions'])} "
                f"({contract['baseline_sessions_available']}/5 תצפיות); IV {fmt(contract['iv']*100 if contract['iv'] is not None else None,1)}%.",
                f"Bid/Ask ${fmt(contract['bid'],2)}/${fmt(contract['ask'],2)}; "
                f"מרווח {fmt(contract['relative_spread']*100 if contract['relative_spread'] is not None else None,1)}%. "
                f"עסקה אחרונה: {contract['last_trade_time']} ET; אין ציטוט מסונכרן לעסקה."])
        if candidate["events"]:
            lines.append("לוח דוחות Nasdaq: " + ", ".join(e["date"] for e in candidate["events"]) + " — לא אומת מול החברה.")
        else:
            lines.append("מועד דוחות מאומת: לא ידוע; אין להסיק שאין אירוע קרוב.")
        if candidate.get("ex_dividend_date"):
            lines.append("Ex-dividend לפי Nasdaq: " + candidate["ex_dividend_date"] + ".")
        alternatives = "ייתכנו מכירת קולים, גידור בפוטים או רגליים של מרווח/גלגול."
        if any(c["dte"] <= 2 for c in candidate["contracts"]):
            alternatives += " סמיכות לפקיעה יכולה להסביר סגירה או גלגול."
        if candidate["events"]:
            alternatives += " אירוע דוחות עשוי להעלות פעילות ו־IV."
        lines.append("הסבר חלופי: " + alternatives)
        evidence = ("נמוכה — ציטוט רחב, ללא ביקוש או לא תקין; קשה לפרש את המחיר והפעילות" if candidate.get("weak_quote") else
            "בינונית — מקור מתוארך ובסיס מחזור השוואתי" if candidate["confidence"] == "Medium" else
            "נמוכה — דגל V/OI ללא תאריך OI ובסיס היסטורי מספק")
        lines.extend(["איכות ראיות: " + evidence + "; אין זיהוי קנייה או פתיחה.", "מקור: " + candidate["source_url"]])
        output.append("\n".join(lines))
    if not report["candidates"]:
        output.append("לא נמצאו מועמדים שעומדים בספי הדוח בשרשראות שנבדקו. זו אינה קביעה שאין פעילות חריגה במניות שלא נבדקו.")
    output.append(f"מגבלות כיסוי: {stats['discovered_names_not_chain_reviewed']:,} שמות שהתגלו לא נבדקו ברמת שרשרת; "
        f"{stats['chains_unavailable']} שרשראות לא זמינות/לא עדכניות, {stats['sources_unavailable']} בקשות מקור לא הושלמו.\n"
        "מקורות הגילוי: OCC (מחזור לפי סוג חשבון) ו־Cboe C1 (בורסה אחת). היחסים ההיסטוריים מחושבים בתוך אותו מקור; לא מחברים אותם למחזורי השרשרת.\n"
        "הבדיקה הבאה: לאמת תנאי חוזה, תאריך OI והשינוי לאחר יום המסחר הבא; נדרש מידע עסקאות מסונכרן כדי להסיק מי יזם קנייה/מכירה.\n"
        "מקורות ונתוני כיסוי מלאים נשמרו בקובצי הדוח של ההרצה.\n"
        "https://github.com/eladyesh/stocks-options-flow-daily/actions")
    return output


def markdown(report):
    blocks = sections(report)
    table = ["| מניה | חוזה / DTE | תאריך / רמת נתון | מחזור / OI / תאריך OI | פרמיה / מכפיל | חריגות | כיוון / איכות | אירוע / הסבר חלופי | מקור |",
             "|---|---|---|---|---|---|---|---|---|"]
    for candidate in report["candidates"]:
        for contract in candidate["contracts"]:
            table.append(f"| {candidate['symbol']} | {contract['side']} {contract['expiration']} ${contract['strike']:g}; DTE {contract['dte']} "
                f"| {report['session']}; chain-level | {fmt(contract['volume'])} / {fmt(contract['open_interest'])} / לא ידוע "
                f"| לא ידוע / לא נמסר | V/OI צילום {multiple(contract['volume_oi_snapshot_ratio'])}; "
                f"מחזור חוזה ×5d {multiple(contract['relative_volume_5_sessions'])} | לא ידוע / {candidate['confidence']} "
                f"| גידור / מרווח / גלגול אפשריים | [Cboe]({candidate['source_url']}) |")
    return "# דוח פעילות אופציות\n\n" + blocks[0] + "\n\n" + "\n".join(table) + "\n\n" + "\n\n---\n\n".join(blocks[1:]) + "\n"


def save(report, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "report.json", report)
    atomic_json(output / "coverage.json", report["coverage"])
    (output / "report.md").write_text(markdown(report))
    (output / "telegram.txt").write_text("\n\n".join(sections(report)) + "\n")
