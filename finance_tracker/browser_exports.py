from __future__ import annotations

import csv
import hashlib
import json
import re
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping
from xml.etree import ElementTree

from .browser_recipes import load_data, load_provider
from .models import money


_DATE_DMY = ("%d/%m/%Y", "%d/%m/%y")
_DATE_EI = ("%b %d, %Y", "%d %b %Y", "%d/%m/%Y")
_MONEY = re.compile(r"^[+-]?[\d,]+(?:\.\d+)?(?:\s*(?:DR|CR))?$", re.I)


def _read_csv(path: Path) -> list[list[str]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp1252")
    return [[cell.strip() for cell in row] for row in csv.reader(text.splitlines())]


def _parse_date(value: str, formats: tuple[str, ...] = _DATE_DMY) -> date | None:
    for format_string in formats:
        try:
            return datetime.strptime(value.strip(), format_string).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _amount(value: str) -> Decimal:
    cleaned = re.sub(r"[^0-9.+-]", "", value.replace(",", ""))
    if not cleaned:
        raise ValueError(f"Invalid amount: {value!r}")
    return abs(money(cleaned))


def _xlsx_rows(path: Path) -> list[list[str]]:
    """Read the first worksheet using only the standard library."""
    namespaces = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("x:si", namespaces):
                shared.append("".join(node.text or "" for node in item.iterfind(".//x:t", namespaces)))
        sheet_name = next(
            (name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")),
            None,
        )
        if not sheet_name:
            raise ValueError("XLSX contains no worksheet")
        root = ElementTree.fromstring(archive.read(sheet_name))
        rows: list[list[str]] = []
        for xml_row in root.findall(".//x:sheetData/x:row", namespaces):
            values: list[str] = []
            for cell in xml_row.findall("x:c", namespaces):
                reference = str(cell.get("r") or "A1")
                letters = re.match(r"[A-Z]+", reference)
                column = 0
                for character in (letters.group(0) if letters else "A"):
                    column = column * 26 + (ord(character) - 64)
                while len(values) < column:
                    values.append("")
                cell_type = cell.get("t")
                if cell_type == "inlineStr":
                    value = "".join(node.text or "" for node in cell.iterfind(".//x:t", namespaces))
                else:
                    value_node = cell.find("x:v", namespaces)
                    value = "" if value_node is None else str(value_node.text or "")
                    if cell_type == "s" and value:
                        value = shared[int(value)]
                values[column - 1] = value.strip()
            rows.append(values)
        return rows


def _adcb_csv(path: Path) -> tuple[list[dict[str, object]], list[str]]:
    card = re.compile(r"^(Primary|Supplementary)\s+Card Number\s*:\s*\S*?(\d{4})-(.+)$", re.I)
    date_pattern = re.compile(r"^\d{2}/\d{2}/\d{4}$")
    current: tuple[str, str] | None = None
    candidates = 0
    parsed = 0
    rows: list[dict[str, object]] = []
    failures: list[str] = []
    for csv_row in _read_csv(path):
        if not csv_row or not any(csv_row):
            current = None
            continue
        match = card.match(csv_row[0])
        if match:
            current = (match.group(1).casefold(), match.group(2))
            continue
        looks_like_date = bool(csv_row and date_pattern.match(csv_row[0]))
        if current and looks_like_date:
            candidates += 1
            if len(csv_row) != 4 or csv_row[2].upper() not in {"DR", "CR"} or not _MONEY.match(csv_row[3]):
                failures.append(",".join(csv_row))
                continue
            when = _parse_date(csv_row[0])
            if not when:
                failures.append(",".join(csv_row))
                continue
            parsed += 1
            rows.append({
                "transaction_date": when.isoformat(),
                "description": " ".join(csv_row[1].split()),
                "amount_aed": str(_amount(csv_row[3])),
                "direction": "CREDIT" if csv_row[2].upper() == "CR" else "DEBIT",
                "account_last4": current[1],
                "card_role": current[0],
            })
            continue
        current = None
    if failures or parsed != candidates:
        raise ValueError(f"ADCB browser CSV parse completeness failed: parsed={parsed} candidates={candidates}")
    if not rows:
        raise ValueError("ADCB browser CSV contained no transactions")
    return rows, [f"Parsed {parsed}/{candidates} transaction rows"]


def _ei_xlsx(path: Path) -> tuple[list[dict[str, object]], list[str]]:
    workbook_rows = _xlsx_rows(path)
    header = ("Date", "Details", "Amount", "Currency", "Debit/Credit", "Status")
    last4: str | None = None
    header_index: int | None = None
    for index, raw in enumerate(workbook_rows):
        cells = (raw + [""] * 6)[:6]
        if not last4:
            match = re.search(r"Card Number\s*:\s*\S*?(\d{4})\s*$", cells[0], re.I)
            if match:
                last4 = match.group(1)
        if tuple(cells) == header:
            header_index = index
            break
    if header_index is None:
        raise ValueError("Emirates Islamic XLSX transaction header was not found")
    candidates = 0
    rows: list[dict[str, object]] = []
    for raw in workbook_rows[header_index + 1:]:
        cells = (raw + [""] * 6)[:6]
        if not any(cells):
            continue
        if not cells[0]:
            continue
        candidates += 1
        when = _parse_date(cells[0], _DATE_EI)
        if not when:
            raise ValueError(f"Emirates Islamic XLSX row has an invalid date: {cells}")
        if cells[4].casefold() not in {"debit", "credit"} or not _MONEY.match(cells[2]):
            raise ValueError(f"Emirates Islamic XLSX row could not be parsed: {cells}")
        currency = (cells[3] or "AED").upper()
        if currency != "AED":
            raise ValueError(
                "Emirates Islamic XLSX foreign-currency row has no evidenced AED equivalent"
            )
        status = cells[5].upper()
        rows.append({
            "transaction_date": when.isoformat(),
            "description": " ".join(cells[1].split()),
            "amount_aed": str(_amount(cells[2])),
            "direction": cells[4].upper(),
            "currency": currency,
            "account_last4": last4,
            "status": status,
            "review_required": status not in {"SETTLED", "COMPLETED", "POSTED"},
        })
    if not rows or len(rows) != candidates:
        raise ValueError(f"Emirates Islamic XLSX parse completeness failed: parsed={len(rows)} candidates={candidates}")
    return rows, [f"Parsed {len(rows)}/{candidates} transaction rows", "Export status retained per row"]


def _fab_csv(path: Path) -> tuple[list[dict[str, object]], list[str]]:
    csv_rows = _read_csv(path)
    preamble: dict[str, str] = {}
    blank_index: int | None = None
    for index, row in enumerate(csv_rows):
        if not row or not any(row):
            blank_index = index
            break
        if len(row) >= 2:
            preamble[row[0]] = row[1]
    if blank_index is None or blank_index + 1 >= len(csv_rows):
        raise ValueError("FAB browser CSV has no preamble separator")
    header = csv_rows[blank_index + 1]
    if not header or not header[0].startswith("Posting Date"):
        raise ValueError("FAB browser CSV transaction header was not found")
    reference = preamble.get("Account Number") or preamble.get("Card Number") or ""
    last4 = reference[-4:] if reference else None
    currency = (preamble.get("Currency") or "AED").upper()
    if currency != "AED":
        raise ValueError("FAB CSV foreign-currency account requires an evidenced AED conversion")
    rows: list[dict[str, object]] = []
    candidates = 0
    for raw in csv_rows[blank_index + 2:]:
        if not raw or not any(raw):
            continue
        when = _parse_date(raw[0])
        if not when:
            raise ValueError(f"FAB browser CSV row has an invalid posting date: {raw}")
        candidates += 1
        debit = raw[3] if len(raw) > 3 else ""
        credit = raw[4] if len(raw) > 4 else ""
        if bool(debit) == bool(credit):
            raise ValueError(f"FAB browser CSV row has ambiguous debit/credit: {raw}")
        rows.append({
            "transaction_date": when.isoformat(),
            "post_date": _parse_date(raw[1]).isoformat() if len(raw) > 1 and _parse_date(raw[1]) else None,
            "description": " ".join((raw[2] if len(raw) > 2 else "").split()),
            "amount_aed": str(_amount(debit or credit)),
            "direction": "DEBIT" if debit else "CREDIT",
            "currency": currency,
            "account_last4": last4,
        })
    if not rows or len(rows) != candidates:
        raise ValueError(f"FAB browser CSV parse completeness failed: parsed={len(rows)} candidates={candidates}")
    return rows, [f"Parsed {len(rows)}/{candidates} transaction rows", "Preamble balance is an as-of snapshot, not a statement closing balance"]


_DATE_HINTS = ("date", "posted", "transaction date", "tx_date", "value date")
_DESC_HINTS = ("description", "narration", "memo", "details", "merchant", "name", "particulars")
_DEBIT_HINTS = ("debit", "withdrawal", "paid out", "dr amount")
_CREDIT_HINTS = ("credit", "deposit", "paid in", "cr amount")
_AMOUNT_HINTS = ("amount", "value")
_DIRECTION_HINTS = ("dr/cr", "cr/dr", "type", "direction", "drcr")


def _column(header: list[str], hints: tuple[str, ...]) -> int | None:
    lowered = [value.casefold() for value in header]
    return next((index for index, value in enumerate(lowered) if any(hint in value for hint in hints)), None)


def _generic_csv(path: Path) -> tuple[list[dict[str, object]], list[str]]:
    csv_rows = _read_csv(path)
    if not csv_rows:
        raise ValueError("Generic browser CSV is empty")
    header, body = csv_rows[0], csv_rows[1:]
    date_index = _column(header, _DATE_HINTS)
    description_index = _column(header, _DESC_HINTS)
    debit_index = _column(header, _DEBIT_HINTS)
    credit_index = _column(header, _CREDIT_HINTS)
    amount_index = _column(header, _AMOUNT_HINTS)
    direction_index = _column(header, _DIRECTION_HINTS)
    if date_index is None or ((debit_index is None or credit_index is None) and amount_index is None):
        raise ValueError(f"Generic browser CSV columns are insufficient: {header}")
    rows: list[dict[str, object]] = []
    candidates = 0
    for raw in body:
        if not raw or not any(raw):
            continue
        when = _parse_date(raw[date_index])
        if not when:
            raise ValueError(f"Generic browser CSV row has an invalid date: {raw}")
        candidates += 1
        direction: str
        amount_value: str
        if debit_index is not None and credit_index is not None:
            debit = raw[debit_index] if debit_index < len(raw) else ""
            credit = raw[credit_index] if credit_index < len(raw) else ""
            if bool(debit) == bool(credit):
                raise ValueError(f"Generic browser CSV row has ambiguous debit/credit: {raw}")
            direction = "DEBIT" if debit else "CREDIT"
            amount_value = debit or credit
        else:
            amount_value = raw[amount_index] if amount_index is not None and amount_index < len(raw) else ""
            if not _MONEY.match(amount_value):
                raise ValueError(f"Generic browser CSV amount is invalid: {amount_value!r}")
            direction_token = raw[direction_index].upper() if direction_index is not None and direction_index < len(raw) else ""
            if direction_token in {"CR", "CREDIT", "C"} or "CR" in amount_value.upper():
                direction = "CREDIT"
            elif direction_token in {"DR", "DEBIT", "D"} or "DR" in amount_value.upper():
                direction = "DEBIT"
            else:
                direction = "CREDIT" if amount_value.strip().startswith("+") else "DEBIT"
        rows.append({
            "transaction_date": when.isoformat(),
            "description": raw[description_index].strip() if description_index is not None and description_index < len(raw) else "Unknown portal transaction",
            "amount_aed": str(_amount(amount_value)),
            "direction": direction,
        })
    if not rows or len(rows) != candidates:
        raise ValueError(f"Generic browser CSV parse completeness failed: parsed={len(rows)} candidates={candidates}")
    return rows, [f"Parsed {len(rows)}/{candidates} transaction rows", "Generic export requires account and currency confirmation"]


_PARSERS: dict[str, Callable[[Path], tuple[list[dict[str, object]], list[str]]]] = {
    "adcb_csv_v1": _adcb_csv,
    "emirates_islamic_xlsx_v1": _ei_xlsx,
    "fab_csv_v1": _fab_csv,
    "generic_csv_v1": _generic_csv,
}


def serialize_capture_for_handoff(capture: Mapping[str, Any]) -> bytes:
    """Serialize one capture to the immutable binary sent to the handoff."""
    return json.dumps(
        capture,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def capture_binary_sha256(capture: Mapping[str, Any]) -> str:
    """Return the SHA-256 of the canonical capture JSON binary."""
    return hashlib.sha256(serialize_capture_for_handoff(capture)).hexdigest()


def _capture_metadata(capture_id: str, captured_at: datetime, source_digest: str) -> dict[str, object]:
    return {
        "capture_contract": {
            "capture_mode": "HEADED_ON_DEMAND",
            "redaction": "REDACTED",
            "immutability": "SHA256_ARCHIVED",
            "handoff_workflow": "INTERACTIVE_ARTIFACT_HANDOFF",
            "actual_mutation": False,
            "cashback_mutation": False,
        },
        "provenance": {
            "capture_id": capture_id,
            "captured_at": captured_at.isoformat(),
            "source_content_sha256": source_digest,
            "hash_algorithm": "SHA-256",
        },
    }


def build_capture_from_export(
    provider_id: str,
    data_id: str,
    source_file: str | Path,
    account: Mapping[str, Any],
    *,
    adapters_root: str | Path | None = None,
    captured_at: datetime | None = None,
    limitations: list[str] | None = None,
) -> dict[str, object]:
    path = Path(source_file)
    if not path.is_file():
        raise ValueError(f"Browser export does not exist: {path}")
    provider = load_provider(provider_id, adapters_root)
    data = load_data(provider_id, data_id, adapters_root)
    source_bytes = path.read_bytes()
    digest = hashlib.sha256(source_bytes).hexdigest()
    capture_id = f"{provider_id}:{data_id}:{digest[:16]}"
    now = captured_at or datetime.now(timezone.utc)
    parser = str(data.get("parser") or "")
    acquisition = str(data.get("acquire") or "")
    if parser in {"wio_credit_v1"} or acquisition in {"download-pdf", "email"}:
        if source_bytes[:5] != b"%PDF-":
            raise ValueError("Statement artifact is not a PDF")
        return {
            "schema_version": 1,
            "capture_id": capture_id,
            **_capture_metadata(capture_id, now, digest),
            "source": {
                "provider": provider["display_name"],
                "site": provider["display_name"],
                "url": provider.get("portal_url"),
                "page_context": data_id,
                "captured_at": now.isoformat(),
                "capture_method": "STATEMENT_DOWNLOAD" if acquisition != "email" else "OFFICIAL_EXPORT",
                "limitations": list(limitations or []),
            },
            "artifact": {
                "kind": "STATEMENT_PDF",
                "source_content_sha256": digest,
                "local_path": str(path.resolve()),
                "file_name": path.name,
                "mime_type": "application/pdf",
                "download_reference": f"sha256:{digest}",
            },
            "account": dict(account),
        }
    if parser not in _PARSERS:
        raise ValueError(f"Browser export parser is not implemented for direct transaction staging: {parser}")
    if str(account.get("currency") or "AED").upper() != "AED":
        raise ValueError("Browser transaction exports require evidenced AED-normalized amounts")
    rows, parser_notes = _PARSERS[parser](path)
    dates = sorted(row["transaction_date"] for row in rows)
    return {
        "schema_version": 1,
        "capture_id": capture_id,
        **_capture_metadata(capture_id, now, digest),
        "source": {
            "provider": provider["display_name"],
            "site": provider["display_name"],
            "url": provider.get("portal_url"),
            "page_context": data_id,
            "captured_at": now.isoformat(),
            "capture_method": "OFFICIAL_EXPORT",
            "date_range": {"start": dates[0], "end": dates[-1]},
            "limitations": [*(limitations or []), *parser_notes],
        },
        "artifact": {
            "kind": "TRANSACTION_ROWS",
            "source_content_sha256": digest,
            "local_path": str(path.resolve()),
            "file_name": path.name,
            "mime_type": (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                if path.suffix.casefold() == ".xlsx"
                else "text/csv"
            ),
            "download_reference": f"sha256:{digest}",
        },
        "account": dict(account),
        "rows": rows,
    }
