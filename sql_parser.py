



"""
import csv

filepath = "your_file.csv"
error_log = "bad_lines.txt"

with open(filepath, "r", encoding="utf-8", errors="replace") as f, \
     open(error_log, "w", encoding="utf-8") as log:
    
    reader = csv.reader(f)
    header = next(reader)
    expected_cols = len(header)
    log.write(f"Expected columns: {expected_cols}\n")
    log.write("=" * 60 + "\n\n")

    bad_count = 0
    for i, row in enumerate(reader, start=2):
        if len(row) != expected_cols:
            bad_count += 1
            log.write(f"Line {i}: got {len(row)} fields\n")
            log.write(f"Raw: {repr(row)}\n")
            log.write("-" * 40 + "\n")

    log.write(f"\nTotal bad lines: {bad_count}\n")

print(f"Done. {bad_count} bad lines written to {error_log}")
SQL Query Parser - Extracts Table & Column Usage from CSV
=========================================================
Reads a CSV with a `sqltextinfo` column, parses every SQL query,
resolves table aliases, and writes an Excel usage-count report.

Strategy (two-tier, zero skips):
  Tier 1 — sqlglot AST parser  : accurate, handles aliases / subqueries
  Tier 2 — regex fallback       : fires when AST fails; extracts what it can
  Neither tier ever skips a query. If both return nothing, the query is still
  counted and noted with a ONE-LINE root-cause summary.

Install:
    pip install pandas sqlglot openpyxl
Run:
    python sql_parser.py input.csv
    python sql_parser.py input.csv --output results.xlsx --column sqltextinfo
"""

import re
import sys
import logging
import traceback
from collections import defaultdict
from pathlib import Path

import pandas as pd
import sqlglot
import sqlglot.expressions as exp

# ─────────────────────────────────────────────────────────────────────────────
# Logging — one clean line per problem, no stack-spam to console
# Full tracebacks go only to sql_parser.log
# ─────────────────────────────────────────────────────────────────────────────
class _OneLiner(logging.Filter):
    """Strip newlines so every console message stays on one line."""
    def filter(self, record):
        record.msg = str(record.msg).replace("\n", " | ").replace("\r", "")
        return True

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.addFilter(_OneLiner())
console_handler.setFormatter(logging.Formatter("%(levelname)s  %(message)s"))

file_handler = logging.FileHandler("sql_parser.log", mode="w", encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

log = logging.getLogger("sql_parser")
log.setLevel(logging.DEBUG)
log.addHandler(console_handler)
log.addHandler(file_handler)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# SQL keywords that cannot be table aliases
_KW = frozenset(
    "WHERE ON SET AND OR INNER LEFT RIGHT OUTER CROSS FULL "
    "SELECT FROM JOIN HAVING GROUP ORDER BY UNION ALL EXCEPT INTERSECT "
    "LIMIT OFFSET FETCH NEXT WITH AS DISTINCT INTO VALUES".split()
)

def _classify_error(exc: Exception) -> str:
    """
    Return a short, human-readable root cause from a sqlglot exception.
    No stack traces — just the essential token / line info.
    """
    msg = str(exc)
    # Pull out "Line X, Col Y" if present
    loc = re.search(r'Line\s+\d+,\s+Col\s*:?\s*\d+', msg)
    # Pull out the unexpected token
    tok = re.search(r'Unexpected token[:\s]+(\S+)', msg, re.IGNORECASE)
    # Pull out "Expected X but got Y"
    exp_got = re.search(r'Expected (.+?) but got (.+?)(?:\.|$)', msg, re.IGNORECASE)

    parts = []
    if exp_got:
        parts.append(f"expected {exp_got.group(1).strip()}, got {exp_got.group(2).strip()}")
    elif tok:
        parts.append(f"unexpected token '{tok.group(1)}'")
    else:
        # Trim the raw message to 120 chars
        parts.append(msg[:120].strip())

    if loc:
        parts.append(loc.group(0))

    return "; ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — CSV reading & query splitting
# ─────────────────────────────────────────────────────────────────────────────

def read_csv(filepath: str) -> pd.DataFrame:
    log.info(f"Reading {filepath}")
    try:
        df = pd.read_csv(
            filepath,
            engine="python",       # tolerates embedded quotes better
            on_bad_lines="warn",
            encoding="utf-8",
            dtype=str,
        )
    except UnicodeDecodeError:
        log.info("UTF-8 failed, retrying with latin-1")
        df = pd.read_csv(filepath, engine="python", on_bad_lines="warn",
                         encoding="latin-1", dtype=str)
    log.info(f"Loaded {len(df):,} rows | columns: {list(df.columns)}")
    return df


def split_queries(cell: str) -> list[str]:
    """
    Split one cell value into individual SQL strings.

    Observed formats (from the screenshot):
      "SELECT ...";\n"SELECT ...";\n...
      or a single bare query without outer quotes.

    We split on  ";  followed by optional whitespace and  "  (next query starts).
    Then strip surrounding quotes / whitespace from each piece.
    """
    if not isinstance(cell, str) or not cell.strip():
        return []

    raw = cell.strip()

    # Primary split: boundary is  ";  <whitespace>  "
    parts = re.split(r'"\s*;\s*"', raw)

    queries = []
    for p in parts:
        q = p.strip().strip('"').strip()
        if q:
            queries.append(q)

    # Fallback: nothing useful → treat whole cell as one query
    if not queries:
        queries = [raw.strip('"').strip()]

    return queries


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — Sanitisation
# ─────────────────────────────────────────────────────────────────────────────

def sanitize(sql: str) -> str:
    """Remove comments, normalise whitespace, drop trailing semicolons."""
    sql = re.sub(r'--[^\n]*', ' ', sql)          # strip -- comments
    sql = re.sub(r'/\*.*?\*/', ' ', sql, flags=re.DOTALL)  # strip /* */ comments
    sql = re.sub(r'[\r\n\t ]+', ' ', sql).strip()
    sql = sql.rstrip(';').strip()
    return sql


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — Tier 1: sqlglot AST parser
# ─────────────────────────────────────────────────────────────────────────────

def _alias_map_from_ast(parsed: exp.Expression) -> dict[str, str]:
    """Build {alias_upper -> real_table_upper} from the AST."""
    m: dict[str, str] = {}
    for node in parsed.walk():
        if isinstance(node, exp.Table):
            name = (node.name or "").upper()
            alias = (node.alias or "").upper()
            if name:
                m[name] = name          # map self
            if alias and name:
                m[alias] = name         # map alias -> real
    return m


def _cols_from_ast(parsed: exp.Expression, aliases: dict[str, str]) -> set[tuple[str, str]]:
    """Extract (real_table, column) pairs from SELECT clauses in the AST."""
    results: set[tuple[str, str]] = set()
    for sel in parsed.find_all(exp.Select):
        for expr in sel.expressions:
            inner = expr.unalias() if hasattr(expr, "unalias") else expr
            if isinstance(inner, exp.Column):
                col = (inner.name or "").upper()
                tbl_ref = (inner.table or "").upper()
                real = aliases.get(tbl_ref, tbl_ref) if tbl_ref else "UNKNOWN"
                if col and col != "*":
                    results.add((real, col))
            elif isinstance(inner, exp.Star):
                tbl_ref = getattr(inner, "table", "") or ""
                real = aliases.get(tbl_ref.upper(), tbl_ref.upper()) if tbl_ref else "UNKNOWN"
                results.add((real, "*"))
    return results


def parse_with_ast(sql: str) -> tuple[set[tuple[str, str]], str | None]:
    """
    Try sqlglot across multiple dialects.
    Returns (pairs_set, None) on success, or (set(), error_summary) on total failure.
    """
    dialects = ("tsql", "spark", "hive", "mysql", None)
    last_err = None

    for dialect in dialects:
        try:
            parsed = sqlglot.parse_one(
                sql,
                read=dialect,
                error_level=sqlglot.ErrorLevel.WARN,  # don't raise on warnings
            )
            if parsed is None:
                continue
            aliases = _alias_map_from_ast(parsed)
            pairs = _cols_from_ast(parsed, aliases)
            # Log to file that we used a fallback dialect
            if dialect != "tsql":
                log.debug(f"Parsed with dialect='{dialect}'")
            return pairs, None

        except Exception as exc:
            last_err = exc
            log.debug(f"Dialect '{dialect}' failed: {exc}")
            continue

    err_summary = _classify_error(last_err) if last_err else "unknown parse error"
    return set(), err_summary


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — Tier 2: Regex fallback (never skips)
# ─────────────────────────────────────────────────────────────────────────────

def parse_with_regex(sql: str) -> set[tuple[str, str]]:
    """
    Best-effort regex extraction when AST parsing fails completely.
    Extracts:
      - Table names from FROM / JOIN clauses
      - Column names from the SELECT list (up to FROM)
    Resolves aliases the same way as the AST tier.
    """
    results: set[tuple[str, str]] = set()

    # ── Build alias map from FROM / JOIN ──────────────────────────────────
    alias_map: dict[str, str] = {}
    table_order: list[str] = []

    tbl_pat = re.compile(
        r'\b(?:FROM|JOIN)\s+'                        # keyword
        r'([A-Za-z_][A-Za-z0-9_.]*)'                # schema.table or table
        r'(?:\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*))?',  # optional alias
        re.IGNORECASE,
    )
    for m in tbl_pat.finditer(sql):
        full = m.group(1)
        alias = (m.group(2) or "").upper()
        short = full.split(".")[-1].upper()
        table_order.append(short)
        alias_map[short] = short
        if alias and alias not in _KW:
            alias_map[alias] = short

    default_table = table_order[0] if table_order else "UNKNOWN"

    # ── Extract SELECT list ───────────────────────────────────────────────
    sel_match = re.search(
        r'\bSELECT\b(.*?)\bFROM\b',
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    if not sel_match:
        # No SELECT..FROM found — nothing to extract
        return results

    select_part = sel_match.group(1)

    for raw_item in select_part.split(","):
        item = raw_item.strip()
        # Drop AS alias at the end:  col AS c  →  col
        item = re.sub(r'\s+AS\s+\S+\s*$', '', item, flags=re.IGNORECASE).strip()
        # Drop DISTINCT keyword
        item = re.sub(r'^\s*DISTINCT\s+', '', item, flags=re.IGNORECASE).strip()
        # Drop inline comments
        item = re.sub(r'--.*', '', item).strip()
        # Skip blanks, *, and function calls (contain parentheses)
        if not item or item == "*" or "(" in item:
            continue

        if "." in item:
            tbl_part, col_part = item.rsplit(".", 1)
            tbl_key = tbl_part.strip().strip('"').strip("`").upper()
            col = col_part.strip().strip('"').strip("`").upper()
            real_tbl = alias_map.get(tbl_key, tbl_key) or default_table
        else:
            col = item.strip().strip('"').strip("`").upper()
            real_tbl = default_table

        # Only accept identifier-like column names
        if col and re.match(r'^[A-Z_][A-Z0-9_#$]*$', col):
            results.add((real_tbl, col))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — Combined parser (Tier 1 → Tier 2, zero skips)
# ─────────────────────────────────────────────────────────────────────────────

def parse_query(sql: str, row_idx: int, q_idx: int) -> set[tuple[str, str]]:
    """
    Parse one SQL query.  Never returns None / never skips.
    Prints ONE clean line to console if the AST tier fails.
    """
    clean = sanitize(sql)
    if not clean:
        return set()

    # Tier 1: AST
    pairs, ast_err = parse_with_ast(clean)

    if ast_err:
        # Print one clean diagnostic line — no spam
        log.warning(
            f"Row {row_idx} Q{q_idx}: AST failed [{ast_err}] — using regex fallback"
        )
        log.debug(f"  SQL was: {clean[:300]}")

        # Tier 2: regex
        pairs = parse_with_regex(clean)

        if not pairs:
            log.warning(
                f"Row {row_idx} Q{q_idx}: regex also yielded nothing "
                f"(non-SELECT statement or unrecognisable structure)"
            )

    return pairs


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6 — Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def process(
    input_csv: str,
    output_excel: str = "column_usage.xlsx",
    sql_col: str = "sqltextinfo",
) -> None:

    df = read_csv(input_csv)

    # Case-insensitive column lookup
    col_map = {c.lower(): c for c in df.columns}
    target = col_map.get(sql_col.lower())
    if not target:
        raise ValueError(
            f"Column '{sql_col}' not found. Available: {list(df.columns)}"
        )

    usage: dict[tuple[str, str], int] = defaultdict(int)
    total = ast_ok = regex_ok = empty = 0

    for row_idx, cell in enumerate(df[target]):
        queries = split_queries(cell)

        for q_idx, q in enumerate(queries, start=1):
            total += 1
            pairs = parse_query(q, row_idx, q_idx)

            if pairs:
                # Track where pairs came from for stats
                _, ast_err = parse_with_ast(sanitize(q))
                if ast_err:
                    regex_ok += 1
                else:
                    ast_ok += 1
                for pair in pairs:
                    usage[pair] += 1
            else:
                empty += 1

        if row_idx > 0 and row_idx % 500 == 0:
            log.info(f"Progress: {row_idx:,} rows processed …")

    # ── Summary ──────────────────────────────────────────────────────────────
    log.info("─" * 60)
    log.info(f"Total queries   : {total:,}")
    log.info(f"AST parsed OK   : {ast_ok:,}")
    log.info(f"Regex fallback  : {regex_ok:,}")
    log.info(f"Yielded nothing : {empty:,}  (non-SELECT / empty / truly unparseable)")
    log.info(f"Unique (tbl,col): {len(usage):,}")
    log.info("─" * 60)

    # ── Write Excel ───────────────────────────────────────────────────────────
    records = [
        {"Table Name": tbl, "Column Name": col, "Usage Count": cnt}
        for (tbl, col), cnt in sorted(usage.items(), key=lambda x: -x[1])
    ]
    out_df = pd.DataFrame(records, columns=["Table Name", "Column Name", "Usage Count"])

    with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
        out_df.to_excel(writer, index=False, sheet_name="Column Usage")
        ws = writer.sheets["Column Usage"]
        for col_cells in ws.columns:
            w = max(len(str(c.value or "")) for c in col_cells) + 4
            ws.column_dimensions[col_cells[0].column_letter].width = min(w, 60)

    log.info(f"Excel written → {output_excel}")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="SQL column-usage extractor — zero skips, clean error output"
    )
    ap.add_argument("input_csv")
    ap.add_argument("--output",  default="column_usage.xlsx")
    ap.add_argument("--column",  default="sqltextinfo")
    args = ap.parse_args()

    process(args.input_csv, args.output, args.column)
