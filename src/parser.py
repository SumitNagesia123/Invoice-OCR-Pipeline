"""
InvoiceOCR Pipeline — Structured parsing.

Extracts structured fields from raw OCR text using regex/heuristic rules.
Outputs a dict matching the invoice_documents + invoice_line_items schema.

Designed as a first-pass heuristic engine; an LLM-assisted mode can be
layered on top later without changing the interface.
"""

import logging
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# ── Field extraction helpers ─────────────────────────────────

def _extract_invoice_number(text: str) -> str | None:
    """
    Look for an invoice/reference number.

    Patterns tried (first match wins):
      - "Invoice No" / "Invoice #" / "Invoice Number" / "Inv #"
      - "Reference" / "Ref" / "Ref #"
      - "Bill No" / "Bill #"
    """
    patterns = [
        # Require an explicit separator (no/number/#/:/.) between keyword and value
        r"(?:invoice|inv)[\s.]*(?:no\.?|number|#|:)[\s.#:]*([A-Z0-9][\w\-/]{1,30})",
        r"(?:reference|ref)[\s.]*(?:no\.?|number|#|:)[\s.#:]*([A-Z0-9][\w\-/]{1,30})",
        r"(?:bill)[\s.]*(?:no\.?|number|#|:)[\s.#:]*([A-Z0-9][\w\-/]{1,30})",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def _extract_date(text: str) -> str | None:
    """
    Look for an invoice / billing date and return it as YYYY-MM-DD.

    Tries keyword-anchored patterns first ("Invoice Date", "Date", "Dated"),
    then falls back to standalone date patterns in the first ~500 chars.

    Supported formats:
      - MM/DD/YYYY, DD/MM/YYYY (heuristic: month > 12 → DD/MM)
      - YYYY-MM-DD
      - DD-Mon-YYYY / Mon DD, YYYY  (e.g. "15 Jan 2024", "Jan 15, 2024")
    """
    month_names = {
        "jan": 1, "january": 1, "feb": 2, "february": 2,
        "mar": 3, "march": 3, "apr": 4, "april": 4,
        "may": 5, "jun": 6, "june": 6,
        "jul": 7, "july": 7, "aug": 8, "august": 8,
        "sep": 9, "september": 9, "oct": 10, "october": 10,
        "nov": 11, "november": 11, "dec": 12, "december": 12,
    }

    def _try_parse_date(raw: str) -> str | None:
        """Attempt to interpret a raw date string as YYYY-MM-DD."""
        raw = raw.strip().rstrip(".")

        # YYYY-MM-DD
        m = re.match(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", raw)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return _safe_date(y, mo, d)

        # MM/DD/YYYY or DD/MM/YYYY
        m = re.match(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})$", raw)
        if m:
            a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if a > 12:
                # a must be day → DD/MM/YYYY
                return _safe_date(y, b, a)
            # default: MM/DD/YYYY
            return _safe_date(y, a, b)

        # DD Mon YYYY  /  Mon DD, YYYY  /  Mon DD YYYY
        m = re.match(
            r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$", raw
        )
        if m:
            d, mon, y = int(m.group(1)), m.group(2).lower(), int(m.group(3))
            mo = month_names.get(mon)
            if mo:
                return _safe_date(y, mo, d)

        m = re.match(
            r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$", raw
        )
        if m:
            mon, d, y = m.group(1).lower(), int(m.group(2)), int(m.group(3))
            mo = month_names.get(mon)
            if mo:
                return _safe_date(y, mo, d)

        return None

    def _safe_date(y: int, m: int, d: int) -> str | None:
        try:
            return datetime(y, m, d).strftime("%Y-%m-%d")
        except ValueError:
            return None

    # --- Keyword-anchored search ---
    anchored = re.search(
        r"(?:invoice\s+date|date|dated|billing\s+date|bill\s+date)"
        r"[\s:]*"
        r"(\d{1,4}[\s/\-][A-Za-z0-9]{1,12}[\s/\-,]*\d{2,4})",
        text, re.IGNORECASE,
    )
    if anchored:
        result = _try_parse_date(anchored.group(1))
        if result:
            return result

    # --- Fallback: first date-like pattern in the first 500 chars ---
    head = text[:500]
    fallback_patterns = [
        r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})",
        r"(\d{1,2}[-/]\d{1,2}[-/]\d{4})",
        r"(\d{1,2}\s+[A-Za-z]+\s+\d{4})",
        r"([A-Za-z]+\s+\d{1,2},?\s+\d{4})",
    ]
    for pat in fallback_patterns:
        m = re.search(pat, head)
        if m:
            result = _try_parse_date(m.group(1))
            if result:
                return result

    return None


def _extract_vendor_name(text: str) -> str | None:
    """
    Look for a vendor / company name near the top of the invoice.

    Strategies:
      1. Keyword anchor: "From:", "Vendor:", "Supplier:", "Sold by:",
         "Bill from:", "Company:"
      2. First non-empty line (many invoices start with the company name).
    """
    # Strategy 1: keyword anchor (word boundary prevents matching
    # substrings like "BestVendor")
    m = re.search(
        r"\b(?:from|vendor|supplier|sold\s+by|bill\s+from|company)"
        r"[\s:]+(.+)",
        text, re.IGNORECASE,
    )
    if m:
        name = m.group(1).strip().split("\n")[0].strip()
        # Remove trailing punctuation
        name = name.rstrip(":;,.")
        if len(name) >= 2:
            return name

    # Strategy 2: first meaningful line (skip blank / very short lines)
    for line in text.splitlines():
        line = line.strip()
        # Skip blank, date-only, or "invoice" header lines
        if not line or len(line) < 3:
            continue
        if re.match(r"^(invoice|page|date|bill|ref)\b", line, re.IGNORECASE):
            continue
        if re.match(r"^\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}$", line):
            continue
        # Use this as the vendor name
        return line

    return None


def _extract_total(text: str) -> tuple[float | None, str | None]:
    """
    Extract the total amount and currency from the invoice text.

    Looks for "Total", "Grand Total", "Amount Due", "Balance Due",
    "Total Due" anchors.  Uses the *last* match so that "Total" at
    the bottom wins over "Subtotal" higher up.

    Returns (amount, currency).
    Currency detection: "$" → USD, "€" → EUR, "£" → GBP,
    else looks for 3-letter ISO code near the amount.
    """
    currency_symbols = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}

    _ISO_CODES = {"USD", "EUR", "GBP", "CAD", "AUD",
                  "JPY", "INR", "CHF", "CNY"}

    # Keyword pattern — use word boundary to avoid matching "Subtotal"
    # for the bare "total" alternative.
    keyword = (
        r"(?:grand\s+total|total\s+due|amount\s+due|balance\s+due"
        r"|total\s+amount|net\s+total|\btotal\b)"
    )

    # After the keyword: optional colon/spaces, optional ISO code,
    # optional currency symbol, then the number.
    pattern = (
        keyword
        + r"[\s:]*"
        + r"(?:([A-Z]{3})\s*)?"           # group 1: optional ISO code
        + r"([$€£¥]?)\s*"   # group 2: optional currency symbol
        + r"([\d,]+\.?\d*)"                # group 3: amount
    )

    matches = list(re.finditer(pattern, text, re.IGNORECASE))
    if not matches:
        return None, None

    # Take the last match (most likely to be the real total)
    m = matches[-1]
    iso_code = m.group(1)
    symbol = m.group(2)
    amount_str = m.group(3).replace(",", "")

    try:
        amount = float(amount_str)
    except ValueError:
        return None, None

    # Determine currency
    currency = currency_symbols.get(symbol)
    if not currency and iso_code and iso_code.upper() in _ISO_CODES:
        currency = iso_code.upper()
    if not currency:
        # Look for ISO code nearby (within 20 chars before/after the match)
        start = max(0, m.start() - 20)
        end = min(len(text), m.end() + 20)
        neighbourhood = text[start:end]
        iso_m = re.search(r"\b([A-Z]{3})\b", neighbourhood)
        if iso_m and iso_m.group(1) in _ISO_CODES:
            currency = iso_m.group(1)
        else:
            currency = "USD"  # sensible default

    return amount, currency


def _extract_line_items(text: str) -> list[dict[str, Any]]:
    """
    Extract line items from a table-like region in the invoice.

    Looks for rows that match the pattern:
        description   quantity   unit_price   line_total
    or:
        quantity   description   unit_price   line_total

    Also handles simpler two-column formats:
        description   amount

    Returns a list of dicts with keys: description, quantity,
    unit_price, line_total. Missing fields are None.
    """
    items: list[dict[str, Any]] = []

    # Pattern: qty  description  unit_price  line_total
    # e.g.  "2    Widget Pro    $25.00    $50.00"
    # or    "Widget Pro    2    25.00    50.00"
    # We try multiple row patterns; each invoice only tends to match one.

    # Pattern A:  description ... qty ... unit_price ... line_total
    # Pattern B:  qty x description ... price ... total
    row_patterns = [
        # qty  description  unit_price  line_total
        re.compile(
            r"^\s*(\d+(?:\.\d+)?)\s+"           # quantity
            r"(.{3,60}?)\s+"                     # description
            r"\$?([\d,]+\.\d{2})\s+"             # unit_price
            r"\$?([\d,]+\.\d{2})\s*$",           # line_total
            re.MULTILINE,
        ),
        # description  qty  unit_price  line_total
        re.compile(
            r"^\s*(.{3,60}?)\s+"                 # description
            r"(\d+(?:\.\d+)?)\s+"                # quantity
            r"\$?([\d,]+\.\d{2})\s+"             # unit_price
            r"\$?([\d,]+\.\d{2})\s*$",           # line_total
            re.MULTILINE,
        ),
        # description   amount  (simple two-column)
        re.compile(
            r"^\s*(.{3,60}?)\s+"                 # description
            r"\$?([\d,]+\.\d{2})\s*$",           # amount only
            re.MULTILINE,
        ),
    ]

    for pat in row_patterns:
        matches = pat.findall(text)
        if not matches:
            continue

        for match in matches:
            if len(match) == 4:
                # Four-group pattern (qty, desc, unit_price, total)
                # or (desc, qty, unit_price, total)
                g = list(match)
                # Distinguish: if first group looks numeric → qty first
                try:
                    float(g[0].replace(",", ""))
                    qty, desc, up, lt = g
                except ValueError:
                    desc, qty, up, lt = g

                items.append({
                    "description": desc.strip(),
                    "quantity": _safe_float(qty),
                    "unit_price": _safe_float(up),
                    "line_total": _safe_float(lt),
                })

            elif len(match) == 2:
                desc, amount = match
                # Skip if description looks like a total/subtotal line
                if re.search(r"(?:total|subtotal|tax|shipping|discount|balance)",
                             desc, re.IGNORECASE):
                    continue
                items.append({
                    "description": desc.strip(),
                    "quantity": None,
                    "unit_price": None,
                    "line_total": _safe_float(amount),
                })

        if items:
            break  # Use the first pattern that found results

    return items


def _safe_float(val: str) -> float | None:
    """Convert a string to float, returning None on failure."""
    try:
        return float(val.replace(",", ""))
    except (ValueError, AttributeError):
        return None


# ── Main parse function ──────────────────────────────────────

def parse_invoice_text(text: str) -> dict[str, Any]:
    """
    Parse raw OCR text into structured invoice fields.

    Returns a dict with:
        invoice_number  — str or None
        invoice_date    — str (YYYY-MM-DD) or None
        vendor_name     — str or None
        total_amount    — float or None
        currency        — str (3-letter ISO) or None
        line_items      — list of dicts (description, quantity,
                          unit_price, line_total)
        confidence      — str: 'high', 'medium', 'low'
                          based on how many key fields were extracted

    Fields that could not be extracted are None.
    """
    invoice_number = _extract_invoice_number(text)
    invoice_date = _extract_date(text)
    vendor_name = _extract_vendor_name(text)
    total_amount, currency = _extract_total(text)
    line_items = _extract_line_items(text)

    # Confidence scoring: count how many key fields were found
    found = sum(1 for v in [invoice_number, invoice_date, vendor_name,
                            total_amount] if v is not None)
    if found >= 3:
        confidence = "high"
    elif found >= 2:
        confidence = "medium"
    else:
        confidence = "low"

    result = {
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "vendor_name": vendor_name,
        "total_amount": total_amount,
        "currency": currency,
        "line_items": line_items,
        "confidence": confidence,
    }

    logger.info(
        "Parsed invoice — confidence=%s, fields_found=%d/4, line_items=%d",
        confidence, found, len(line_items),
    )
    return result
