# =============================================================================
# CSV Auto-Detect / Convert Layer
#
# Supports THREE input schemas and normalizes all of them into the format
# STEP 1 expects:
#
#       SqlTextInfo,Metric_Date,users
#       "<multiline SQL ending in ;>","YYYY-MM-DD",USERID
#
# ── Format A: "new_format" ───────────────────────────────────────────────
#   Columns: user_name, Db_nm, Tbl_nm, SqlTextInfo, LogDate, StartTime,
#            LastResponseTime
#   Detection: header contains "user_name" AND "logdate"
#   Parsed with csv.DictReader (handles quoted multiline cells).
#
# ── Format B: "standard_format" (already what STEP 1 expects) ───────────
#   Columns: SqlTextInfo, Metric_Date, users
#   Record layout: "<multiline SQL>;","YYYY-MM-DD",USERID   (quoted, with ;)
#   Detection: header matches SqlTextInfo/Metric_Date/users AND the first
#              data row already starts with a double quote.
#   No conversion needed — passed through as-is.
#
# ── Format C: "unquoted_format" (NEW) ────────────────────────────────────
#   Columns: SqlTextInfo, Metric_Date, users
#   Record layout (no quoting, no trailing ;):
#       select * from t1,29/02/2026,user1
#   Detection: header matches SqlTextInfo/Metric_Date/users AND the first
#              data row does NOT start with a double quote.
#   Conversion: since the line is unquoted, we cannot split on every comma
#               (the SQL itself may legitimately contain commas). Instead we
#               rsplit(",", 2) from the RIGHT — the last two comma-separated
#               fields are always Metric_Date and users (neither of which
#               contains commas); everything left of that, however many
#               commas it has, is the SQL text. We then wrap it in double
#               quotes, escape any internal quotes, and append ";" if
#               missing — producing a row identical in shape to Format B.
#
# SQL preservation rules (all formats):
#   • All internal newlines, commas, and quotes inside the SQL are kept.
#   • SQL is guaranteed to end with exactly one ";" (required by RECORD_RE).
#   • Temp file auto-deleted on process exit via atexit.
# =============================================================================

import csv
import os
import tempfile
import atexit


def _peek_lines(csv_path: str, n: int = 2):
    """Return up to the first n non-empty lines of the file (raw, unstripped-newline)."""
    lines = []
    with open(csv_path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                lines.append(line)
            if len(lines) >= n:
                break
    return lines


def _detect_format(csv_path: str) -> str:
    """
    Returns one of: "new_format", "standard_format", "unquoted_format".
    """
    lines = _peek_lines(csv_path, 2)
    if not lines:
        return "standard_format"  # nothing to do, let STEP 1 handle empty file

    header_clean = lines[0].strip().lower().replace('"', '')

    # Format A -----------------------------------------------------------
    if "user_name" in header_clean and "logdate" in header_clean:
        return "new_format"

    # Formats B / C share the same header; disambiguate via first data row
    if "sqltextinfo" in header_clean and "metric_date" in header_clean and "users" in header_clean:
        if len(lines) < 2:
            # header only, no data rows — nothing to convert either way
            return "standard_format"
        first_data_row = lines[1].lstrip()
        if first_data_row.startswith('"'):
            return "standard_format"
        else:
            return "unquoted_format"

    # Fallback — treat as already-correct standard format
    return "standard_format"


import re as _re


def _normalize_date(date_str: str) -> str:
    """
    Normalize a date string to YYYY-MM-DD, the format STEP 1's RECORD_RE
    requires. Handles:
      • YYYY-MM-DD                          -> unchanged
      • YYYY-MM-DDTHH:MM:SS(.ffffff)?Z?      -> date part only
                                                (e.g. 2026-06-25T14:48:39.466Z
                                                 -> 2026-06-25)
      • DD/MM/YYYY                           -> rearranged (e.g. 29/02/2026 -> 2026-02-29)
      • YYYY/MM/DD                           -> rearranged
    Any other/unrecognized shape is returned unchanged (STEP 1 will simply
    fail to match it and it'll be excluded — same as before this fix, but
    now only for genuinely unexpected formats instead of every row).
    """
    date_str = date_str.strip()

    if _re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return date_str

    # ISO 8601 timestamp, e.g. 2026-06-25T14:48:39.466Z — keep date part only
    m = _re.match(r'^(\d{4}-\d{2}-\d{2})[T ]\d{2}:\d{2}:\d{2}', date_str)
    if m:
        return m.group(1)

    m = _re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', date_str)
    if m:
        dd, mm, yyyy = m.groups()
        return f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}"  # assumes DD/MM/YYYY

    m = _re.match(r'^(\d{4})/(\d{1,2})/(\d{1,2})$', date_str)
    if m:
        yyyy, mm, dd = m.groups()
        return f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}"

    return date_str  # unrecognized — left as-is


def _write_temp_csv(rows_out, source_label: str) -> str:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8"
    )
    tmp.write("SqlTextInfo,Metric_Date,users\n")
    for rec in rows_out:
        tmp.write(rec + "\n")
    tmp.close()

    atexit.register(os.remove, tmp.name)
    print(f"[INFO] {source_label} detected — converted {len(rows_out)} rows -> {tmp.name}")
    return tmp.name


def _convert_new_csv_to_input_format(src_path: str) -> str:
    """
    Parse the new-format CSV with csv.DictReader (handles quoted multiline
    cells natively) and emit a temp file in the format STEP 1 expects.
    """
    rows_out = []

    with open(src_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            sql_raw  = (row.get("SqlTextInfo") or "").strip()
            log_date = _normalize_date((row.get("LogDate") or "").strip())
            user     = (row.get("user_name") or "").strip()

            if not sql_raw or not log_date or not user:
                continue          # skip incomplete / header-only rows

            # Guarantee exactly one trailing semicolon
            sql_norm = sql_raw.rstrip()
            if not sql_norm.endswith(";"):
                sql_norm += ";"

            # Escape internal double-quotes (CSV: " -> "")
            sql_esc = sql_norm.replace('"', '""')

            # Assemble record exactly as RECORD_RE expects
            rows_out.append(f'"{sql_esc}","{log_date}",{user}')

    return _write_temp_csv(rows_out, "New CSV schema")


def _convert_unquoted_csv_to_input_format(src_path: str) -> str:
    """
    Parse the unquoted SqlTextInfo,Metric_Date,users CSV, e.g.:

        SqlTextInfo,Metric_Date,users
        select * from t1,29/02/2026,user1

    There is no quoting, so we can't split naively on every comma (the SQL
    text itself may contain commas). We rsplit(",", 2) from the right: the
    last field is always `users`, the second-to-last is always
    `Metric_Date`, and everything remaining on the left — regardless of how
    many commas it contains — is the SQL text.

    Output rows are quoted, quote-escaped, and semicolon-terminated so they
    match the standard_format shape expected by STEP 1.
    """
    rows_out = []

    with open(src_path, "r", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header_skipped = False
        for raw_fields in reader:
            if not raw_fields or not any(f.strip() for f in raw_fields):
                continue  # skip blank lines

            line = ",".join(raw_fields)

            if not header_skipped:
                header_skipped = True
                continue  # skip header row

            parts = line.rsplit(",", 2)
            if len(parts) != 3:
                # Malformed row — not enough fields to isolate date + user
                continue

            sql_raw, log_date, user = parts
            sql_raw  = sql_raw.strip()
            log_date = _normalize_date(log_date.strip())
            user     = user.strip()

            if not sql_raw or not log_date or not user:
                continue

            # Guarantee exactly one trailing semicolon
            sql_norm = sql_raw.rstrip()
            if not sql_norm.endswith(";"):
                sql_norm += ";"

            # Escape internal double-quotes (CSV: " -> "")
            sql_esc = sql_norm.replace('"', '""')

            rows_out.append(f'"{sql_esc}","{log_date}",{user}')

    return _write_temp_csv(rows_out, "Unquoted CSV schema")


# ── Auto-convert (must run BEFORE STEP 1 opens INPUT_CSV) ────────────────────
_fmt = _detect_format(INPUT_CSV)

if _fmt == "new_format":
    INPUT_CSV = _convert_new_csv_to_input_format(INPUT_CSV)
elif _fmt == "unquoted_format":
    INPUT_CSV = _convert_unquoted_csv_to_input_format(INPUT_CSV)
else:
    print("[INFO] Standard CSV format detected — no conversion needed.")
