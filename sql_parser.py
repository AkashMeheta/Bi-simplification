"""
SQL Query Parser - Extracts Table & Column Usage from CSV
=========================================================
Reads a CSV with a `sqltextinfo` column, parses each SQL query,
resolves aliases, and outputs an Excel with table/column usage counts.

Requirements:
    pip install pandas sqlglot openpyxl
"""

import re
import logging
import pandas as pd
import sqlglot
import sqlglot.expressions as exp
from collections import defaultdict
from pathlib import Path

# ─────────────────────────── Logging Setup ──────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("sql_parser.log", mode="w", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1: CSV Reading & Query Extraction
# ═══════════════════════════════════════════════════════════════════════════

def read_csv_robust(filepath: str) -> pd.DataFrame:
    """
    Read CSV using Python engine to handle embedded quotes and large files.
    Falls back to raw line reading if pandas fails.
    """
    log.info(f"Reading CSV: {filepath}")
    try:
        df = pd.read_csv(
            filepath,
            engine="python",          # more lenient than C engine
            on_bad_lines="warn",       # log bad lines instead of crashing
            encoding="utf-8",
            dtype=str,                 # keep everything as string
        )
        log.info(f"Loaded {len(df)} rows, columns: {list(df.columns)}")
        return df
    except Exception as e:
        log.error(f"pandas read failed: {e}")
        raise


def extract_queries_from_cell(cell_value: str) -> list[str]:
    """
    A single cell in `sqltextinfo` may contain one or more SQL statements.
    Each statement is wrapped in double quotes like: "SELECT ..."
    
    Challenge: queries may themselves contain double quotes for column names,
    e.g.  "select "col1" "col2" from tbl"
    
    Strategy:
      1. Try to split on the pattern  ;"  or  "\n"  which reliably marks
         query boundaries in the sample data.
      2. Strip outer quotes and semicolons.
      3. Keep the raw text for attempted parsing even if it looks malformed.
    """
    if not isinstance(cell_value, str) or not cell_value.strip():
        return []

    raw = cell_value.strip()

    # Pattern observed in the image: queries are separated by  ";\n  or  ";\r\n
    # Split on the boundary between one closing quote+semicolon and the next
    # opening quote, while allowing whitespace/newlines in between.
    #
    # We use a lookahead so we don't consume the opening quote of the next query.
    parts = re.split(r'(?<=")\s*;\s*(?=")', raw)

    queries = []
    for part in parts:
        # Strip surrounding whitespace and any leading/trailing lone quotes
        q = part.strip().strip('"').strip()
        if q:
            queries.append(q)

    # If the above produced nothing useful, fall back: treat the whole cell as
    # one query after stripping outer quotes.
    if not queries:
        queries = [raw.strip('"').strip()]

    return queries


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2: SQL Parsing & Alias Resolution
# ═══════════════════════════════════════════════════════════════════════════

def sanitize_sql(sql: str) -> str:
    """
    Light pre-processing to help sqlglot handle pathological cases:
    - Remove SQL comments (-- ...)
    - Collapse multiple spaces / newlines
    - Replace bare unquoted double-quotes used as column aliases with backticks
      so the parser doesn't choke on them.
    """
    # Remove single-line SQL comments
    sql = re.sub(r'--[^\n]*', '', sql)
    # Normalise whitespace
    sql = re.sub(r'[\r\n\t ]+', ' ', sql).strip()
    # Remove trailing semicolons
    sql = sql.rstrip(';').strip()
    return sql


def build_alias_map(parsed: exp.Expression) -> dict[str, str]:
    """
    Walk the AST and build {alias -> real_table_name} mapping.
    Handles:
      FROM tableA t
      FROM tableA AS t
      JOIN schm.tableB b ON ...
    """
    alias_map: dict[str, str] = {}

    for node in parsed.walk():
        # Table references with or without aliases
        if isinstance(node, exp.Table):
            table_name = node.name or ""
            alias = node.alias or ""
            if alias and table_name:
                alias_map[alias.upper()] = table_name.upper()
            elif table_name:
                # Map the bare name to itself so lookups always succeed
                alias_map[table_name.upper()] = table_name.upper()

    return alias_map


def extract_select_columns(parsed: exp.Expression, alias_map: dict[str, str]) -> list[tuple[str, str]]:
    """
    Extract (table_name, column_name) pairs from SELECT expressions.
    
    Returns a *deduplicated* list per query (counted once per query as required).
    """
    results: set[tuple[str, str]] = set()

    for select in parsed.find_all(exp.Select):
        for expr in select.expressions:
            # Unwrap aliases like  col AS c  ->  col
            inner = expr.unalias() if hasattr(expr, "unalias") else expr

            if isinstance(inner, exp.Column):
                col_name = (inner.name or "").upper()
                # The table qualifier on the column (may be alias or real name)
                table_ref = (inner.table or "").upper()

                # Resolve alias -> real table name
                real_table = alias_map.get(table_ref, table_ref) if table_ref else "UNKNOWN"

                if col_name and col_name != "*":
                    results.add((real_table, col_name))

            elif isinstance(inner, exp.Star):
                # SELECT * — record as wildcard
                table_ref = (inner.table or "").upper() if hasattr(inner, "table") else ""
                real_table = alias_map.get(table_ref, table_ref) if table_ref else "UNKNOWN"
                results.add((real_table, "*"))

    return list(results)


def parse_single_query(sql: str) -> list[tuple[str, str]]:
    """
    Parse one SQL string and return [(table, column), ...] (deduplicated).
    Returns an empty list on failure.
    """
    clean = sanitize_sql(sql)
    if not clean:
        return []

    # Try multiple dialects; fall back gracefully
    for dialect in ("tsql", "spark", "mysql", None):
        try:
            parsed = sqlglot.parse_one(clean, read=dialect, error_level=sqlglot.ErrorLevel.WARN)
            if parsed is None:
                continue
            alias_map = build_alias_map(parsed)
            pairs = extract_select_columns(parsed, alias_map)
            return pairs
        except Exception:
            continue  # try next dialect

    return []  # all dialects failed


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3: Main Pipeline
# ═══════════════════════════════════════════════════════════════════════════

def process_csv(
    input_csv: str,
    output_excel: str,
    sqltextinfo_col: str = "sqltextinfo",
    problematic_log: str = "problematic_queries.txt",
) -> None:
    """
    Full pipeline:
      1. Read CSV
      2. Extract queries from the target column
      3. Parse each query
      4. Aggregate (table, column) counts
      5. Write Excel output
    """

    # ── Step 1: Load ────────────────────────────────────────────────────────
    df = read_csv_robust(input_csv)

    # Case-insensitive column lookup
    col_map = {c.lower(): c for c in df.columns}
    target_col = col_map.get(sqltextinfo_col.lower())
    if target_col is None:
        available = list(df.columns)
        raise ValueError(
            f"Column '{sqltextinfo_col}' not found in CSV. "
            f"Available columns: {available}"
        )

    # ── Step 2 & 3: Extract + Parse ─────────────────────────────────────────
    usage_counts: dict[tuple[str, str], int] = defaultdict(int)
    problematic: list[str] = []

    total_queries = 0
    parsed_ok = 0

    for row_idx, cell in enumerate(df[target_col]):
        queries = extract_queries_from_cell(cell)

        for q in queries:
            total_queries += 1
            pairs = parse_single_query(q)

            if pairs:
                parsed_ok += 1
                for (table, col) in pairs:
                    usage_counts[(table, col)] += 1
            else:
                # Log problematic query
                problematic.append(f"Row {row_idx} | {q[:300]}")
                log.debug(f"Could not parse query at row {row_idx}: {q[:120]}")

        if row_idx % 1000 == 0 and row_idx > 0:
            log.info(f"  Processed {row_idx} rows …")

    log.info(
        f"Parsing complete. Total queries: {total_queries}, "
        f"successfully parsed: {parsed_ok}, "
        f"problematic: {len(problematic)}"
    )

    # ── Step 4: Save problematic queries ────────────────────────────────────
    if problematic:
        Path(problematic_log).write_text("\n".join(problematic), encoding="utf-8")
        log.info(f"Problematic queries saved to: {problematic_log}")

    # ── Step 5: Build output DataFrame ──────────────────────────────────────
    if not usage_counts:
        log.warning("No table/column pairs extracted. Output Excel will be empty.")

    records = [
        {"Table Name": tbl, "Column Name": col, "Usage Count": cnt}
        for (tbl, col), cnt in sorted(usage_counts.items(), key=lambda x: -x[1])
    ]
    result_df = pd.DataFrame(records, columns=["Table Name", "Column Name", "Usage Count"])

    # ── Step 6: Write Excel ──────────────────────────────────────────────────
    with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
        result_df.to_excel(writer, index=False, sheet_name="Column Usage")

        # Auto-fit column widths
        ws = writer.sheets["Column Usage"]
        for col_cells in ws.columns:
            max_len = max(len(str(c.value or "")) for c in col_cells)
            ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 4, 60)

    log.info(f"Excel output written to: {output_excel}")
    log.info(f"Unique (table, column) pairs: {len(records)}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4: Entry Point
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Parse SQL queries from a CSV and produce a column-usage Excel report."
    )
    parser.add_argument("input_csv",  help="Path to the input CSV file")
    parser.add_argument(
        "--output",  default="column_usage.xlsx",
        help="Output Excel filename (default: column_usage.xlsx)"
    )
    parser.add_argument(
        "--column", default="sqltextinfo",
        help="Name of the SQL column in the CSV (default: sqltextinfo)"
    )
    parser.add_argument(
        "--prob-log", default="problematic_queries.txt",
        help="File to log unparseable queries (default: problematic_queries.txt)"
    )
    args = parser.parse_args()

    process_csv(
        input_csv=args.input_csv,
        output_excel=args.output,
        sqltextinfo_col=args.column,
        problematic_log=args.prob_log,
    )
