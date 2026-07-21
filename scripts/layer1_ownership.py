"""
Layer 1d -- SEC Schedule 13D/G beneficial ownership context.

This module reads public SEC EDGAR endpoints only. It is opt-in from the CLI
because 13D/G data is contextual, not core technical-analysis input.
"""
import json
import os
import re
from datetime import date, datetime, timedelta
from html import unescape
from urllib.request import Request, urlopen


SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "margin-ta-hermes/1.0 research@example.com")
SEC_BENEFICIAL_OWNERSHIP_FORMS = {"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}
_TICKER_CIK_CACHE: dict[str, str] | None = None


def _fetch_text(url: str, timeout: int = 12) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": SEC_USER_AGENT,
            "Accept": "application/json,text/plain,text/html",
        },
    )
    with urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _fetch_json(url: str, timeout: int = 12) -> dict:
    return json.loads(_fetch_text(url, timeout=timeout))


def _parse_date(value) -> date | None:
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _safe_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=None):
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return default


def _filing_text(raw_text: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", raw_text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _first_regex(text: str, patterns: list[str], flags: int = re.IGNORECASE) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return match.group(1).strip(" .:-\t\r\n")
    return None


def _clean_reporting_owner_name(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = re.sub(
        r"^I\.R\.S\.\s+IDENTIFICATION\s+NO(?:S)?\.?\s+OF\s+ABOVE\s+PERSON(?:S)?(?:\s+\(ENTITIES ONLY\))?\s+",
        "",
        name,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+-\s+\d{2}-\d{7}.*$", "", cleaned)
    cleaned = re.sub(r"\s+\d{2}-\d{7}.*$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .:-")
    return cleaned or None


def _normalized_owner_key(name: str | None) -> str:
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _detect_13g_rule(text: str) -> str | None:
    checked = r"(?:\bX\b|\[[xX]\]|☒|&#9746;|&#x2612;)"
    for rule in ("13d-1(b)", "13d-1(c)", "13d-1(d)"):
        if re.search(rf"{checked}.{{0,80}}Rule\s+{re.escape(rule)}", text, flags=re.IGNORECASE):
            return rule
        if re.search(rf"Rule\s+{re.escape(rule)}.{{0,80}}{checked}", text, flags=re.IGNORECASE):
            return rule
    return None


def _classify_owner(form_type: str, text: str) -> tuple[str, bool]:
    upper_form = form_type.upper()
    if upper_form.startswith("SC 13D"):
        return "active_or_control", True
    rule = _detect_13g_rule(text)
    if rule == "13d-1(b)":
        return "qualified_institutional", False
    if rule == "13d-1(c)":
        return "passive_investor", False
    if rule == "13d-1(d)":
        return "exempt_holder", False
    return "passive_or_exempt", False


def _sec_archive_document_url(cik: str, accession_number: str, primary_document: str) -> str | None:
    if not accession_number or not primary_document:
        return None
    accession_dir = accession_number.replace("-", "")
    document_path = primary_document.lstrip("/")
    if document_path.startswith("xslF345") and "/" in document_path:
        document_path = document_path.rsplit("/", 1)[-1]
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_dir}/{document_path}"


def _ticker_cik_map() -> dict[str, str]:
    global _TICKER_CIK_CACHE
    if _TICKER_CIK_CACHE is not None:
        return _TICKER_CIK_CACHE
    raw = _fetch_json("https://www.sec.gov/files/company_tickers.json")
    mapping: dict[str, str] = {}
    for row in raw.values():
        ticker = str(row.get("ticker") or "").upper()
        cik = row.get("cik_str")
        if ticker and cik is not None:
            mapping[ticker] = str(cik).zfill(10)
    _TICKER_CIK_CACHE = mapping
    return mapping


def _sec_cik_for_ticker(symbol: str) -> str | None:
    return _ticker_cik_map().get(symbol.upper())


def _sec_company_submissions(cik: str) -> dict:
    return _fetch_json(f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json")


def parse_beneficial_ownership_filing(
    raw_text: str,
    symbol: str,
    form_type: str,
    filing_date: str,
    accession_number: str,
    filing_url: str,
) -> dict:
    text = _filing_text(raw_text)
    owner_name = _first_regex(
        text,
        [
            r"\b1\s+NAMES? OF REPORTING (?:PERSON|PERSONS)\s+(?:I\.R\.S\.\s+IDENTIFICATION\s+NO(?:S)?\.?\s+OF\s+ABOVE\s+PERSON(?:S)?(?:\s+\(ENTITIES ONLY\))?\s+)?(.+?)(?:\s+2\s+CHECK|\s+2\.?\s+CHECK|\s+CHECK THE APPROPRIATE BOX)",
            r"NAME OF REPORTING (?:PERSON|PERSONS)\s+(?:I\.R\.S\.\s+IDENTIFICATION\s+NO(?:S)?\.?\s+OF\s+ABOVE\s+PERSON(?:S)?(?:\s+\(ENTITIES ONLY\))?\s+)?(.+?)(?:\s+2\.?\s+CHECK|\s+CHECK THE APPROPRIATE BOX|\s+SEC USE ONLY|\s+4\s+SOURCE)",
            r"\(1\)\s+Names? of reporting persons?\s+(.+?)\s+\(2\)\s+Check",
            r"2\(a\)\s*Name of person filing:?\s*-+\s*(.+?)(?:\s+2\(b\)|\s+Address or principal business office)",
            r"Name of Reporting Person\s+(.+?)(?:\s+I\.R\.S\.|\s+2\s+Check|\s+Check the Appropriate)",
        ],
    )
    owner_name = _clean_reporting_owner_name(owner_name)
    percent = _safe_float(
        _first_regex(
            text,
            [
                r"PERCENT OF CLASS REPRESENTED BY AMOUNT IN ROW\s*\(?1?1?\)?\s+([0-9][0-9,]*(?:\.[0-9]+)?%)",
                r"\(\d+\)\s+Percent of class represented by amount in Row\s+\(?\d+\)?\s+([0-9][0-9,]*(?:\.[0-9]+)?%)",
                r"Percent of class represented by amount in Row\s+\d+\s+([0-9][0-9,]*(?:\.[0-9]+)?%)",
                r"percent of the class[^.]{0,160}?([0-9][0-9,]*(?:\.[0-9]+)?%)",
                r"represented\s+approximately\s+([0-9][0-9,]*(?:\.[0-9]+)?%)",
            ],
        )
    )
    shares = _safe_int(
        _first_regex(
            text,
            [
                r"AGGREGATE AMOUNT BENEFICIALLY OWNED BY EACH REPORTING PERSON\s+([0-9][0-9,]*)",
                r"Amount beneficially owned:\s*-+\s*([0-9][0-9,]*)",
                r"amount beneficially owned[^0-9]{0,80}([0-9][0-9,]*)",
            ],
        )
    )
    owner_type, activist_intent = _classify_owner(form_type, text)
    dropped_below_5pct = bool(percent is not None and percent < 5.0)
    if not dropped_below_5pct:
        dropped_below_5pct = bool(
            re.search(
                r"ceased to be the beneficial owner of more than 5 percent.{0,120}(\[[xX]\]|☒|&#9746;|&#x2612;)",
                text,
                flags=re.IGNORECASE,
            )
        )
    purpose_excerpt = _first_regex(
        text,
        [
            r"Item\s+4\.?\s+Purpose of Transaction\.?\s+(.+?)(?:Item\s+5\.|Item\s+5\s+Interest|SIGNATURE)",
            r"ITEM\s+4\.?\s+PURPOSE OF TRANSACTION\.?\s+(.+?)(?:ITEM\s+5\.|SIGNATURE)",
        ],
    )
    event = {
        "symbol": symbol,
        "form_type": form_type,
        "event_type": "amendment" if form_type.upper().endswith("/A") else "new",
        "filing_date": filing_date,
        "accession_number": accession_number,
        "filing_url": filing_url,
        "reporting_owner": owner_name,
        "beneficial_shares": shares,
        "ownership_pct": percent,
        "owner_type": owner_type,
        "activist_intent_flag": activist_intent,
        "passive_holder_flag": owner_type in {"qualified_institutional", "passive_investor", "exempt_holder", "passive_or_exempt"},
        "dropped_below_5pct_flag": dropped_below_5pct,
        "purpose_excerpt": purpose_excerpt[:500] if purpose_excerpt else None,
        "provider": "sec_edgar_13d_g",
    }
    return {key: value for key, value in event.items() if value is not None}


def _annotate_events(events: list[dict]) -> list[dict]:
    by_owner: dict[str, dict] = {}
    annotated: list[dict] = []
    for event in sorted(events, key=lambda item: str(item.get("filing_date") or ""), reverse=False):
        owner_key = _normalized_owner_key(event.get("reporting_owner"))
        previous = by_owner.get(owner_key)
        if previous:
            current_pct = _safe_float(event.get("ownership_pct"))
            previous_pct = _safe_float(previous.get("ownership_pct"))
            current_shares = _safe_float(event.get("beneficial_shares"))
            previous_shares = _safe_float(previous.get("beneficial_shares"))
            if current_pct is not None and previous_pct is not None:
                event["ownership_delta_pct"] = round(current_pct - previous_pct, 2)
            if current_shares is not None and previous_shares is not None:
                event["beneficial_shares_delta"] = int(current_shares - previous_shares)
            previous_form = str(previous.get("form_type") or "").upper()
            current_form = str(event.get("form_type") or "").upper()
            if previous_form.startswith("SC 13G") and current_form.startswith("SC 13D"):
                event["converted_13g_to_13d_flag"] = True
        annotated.append(event)
        if owner_key:
            by_owner[owner_key] = event
    return sorted(annotated, key=lambda item: str(item.get("filing_date") or ""), reverse=True)


def summarize_beneficial_ownership(events: list[dict]) -> dict:
    events = sorted(events, key=lambda item: str(item.get("filing_date") or ""), reverse=True)
    return {
        "source": "sec_edgar_13d_g",
        "event_count": len(events),
        "active_13d_count": sum(1 for event in events if str(event.get("form_type") or "").upper().startswith("SC 13D")),
        "passive_13g_count": sum(1 for event in events if str(event.get("form_type") or "").upper().startswith("SC 13G")),
        "drop_below_5pct_count": sum(1 for event in events if event.get("dropped_below_5pct_flag")),
        "conversion_13g_to_13d_count": sum(1 for event in events if event.get("converted_13g_to_13d_flag")),
        "latest_event": events[0] if events else None,
        "events": events[:12],
    }


def fetch_beneficial_ownership(symbol: str, lookback_days: int = 365, max_filings: int = 60) -> dict:
    """Fetch recent SEC 13D/G events and annotate owner-level stake changes."""
    symbol = symbol.upper()
    end = datetime.utcnow().date()
    start = end - timedelta(days=max(1, int(lookback_days)))
    warnings: list[str] = []

    try:
        cik = _sec_cik_for_ticker(symbol)
    except Exception as exc:
        summary = summarize_beneficial_ownership([])
        summary.update({"symbol": symbol, "status": "failed", "warnings": [f"SEC ticker mapping failed: {exc}"]})
        return summary

    if not cik:
        summary = summarize_beneficial_ownership([])
        summary.update({"symbol": symbol, "status": "missing", "warnings": ["SEC ticker-to-CIK mapping unavailable"]})
        return summary

    try:
        submissions = _sec_company_submissions(cik)
        recent = ((submissions.get("filings") or {}).get("recent") or {})
    except Exception as exc:
        summary = summarize_beneficial_ownership([])
        summary.update({"symbol": symbol, "status": "failed", "warnings": [f"SEC submissions fetch failed: {exc}"]})
        return summary

    forms = recent.get("form") if isinstance(recent.get("form"), list) else []
    filing_dates = recent.get("filingDate") if isinstance(recent.get("filingDate"), list) else []
    accessions = recent.get("accessionNumber") if isinstance(recent.get("accessionNumber"), list) else []
    primary_documents = recent.get("primaryDocument") if isinstance(recent.get("primaryDocument"), list) else []

    parsed_events: list[dict] = []
    failed_filings = 0
    candidate_count = 0
    row_count = min(len(forms), len(filing_dates), len(accessions), len(primary_documents))
    for index in range(row_count):
        form_type = str(forms[index] or "").upper()
        if form_type not in SEC_BENEFICIAL_OWNERSHIP_FORMS:
            continue
        parsed_filing_date = _parse_date(filing_dates[index])
        if parsed_filing_date is None or parsed_filing_date > end:
            continue
        candidate_count += 1
        filing_date = str(filing_dates[index] or "")
        accession_number = str(accessions[index] or "")
        primary_document = str(primary_documents[index] or "")
        filing_url = _sec_archive_document_url(cik, accession_number, primary_document)
        if not filing_url:
            failed_filings += 1
            continue
        try:
            raw_text = _fetch_text(filing_url)
            parsed_events.append(parse_beneficial_ownership_filing(raw_text, symbol, form_type, filing_date, accession_number, filing_url))
        except Exception:
            failed_filings += 1
        if len(parsed_events) >= max_filings:
            break

    annotated = _annotate_events(parsed_events)
    window_events = [
        event for event in annotated
        if (parsed_date := _parse_date(event.get("filing_date"))) is not None and start <= parsed_date <= end
    ]
    summary = summarize_beneficial_ownership(window_events)
    summary.update(
        {
            "symbol": symbol,
            "status": "ok" if parsed_events or candidate_count == 0 else "failed",
            "lookback_days": lookback_days,
            "historical_events_parsed": len(parsed_events),
            "candidate_filings": candidate_count,
        }
    )
    if failed_filings:
        warnings.append(f"SEC 13D/G fetch/parse failed for {failed_filings} filing(s)")
    if candidate_count and not parsed_events:
        warnings.append("SEC 13D/G filings found but no documents could be parsed")
    summary["warnings"] = warnings
    return summary
