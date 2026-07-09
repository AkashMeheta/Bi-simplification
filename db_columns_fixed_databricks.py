# Databricks notebook source
# =============================================================================
# Teradata Query Column-Usage Metrics Pipeline — Databricks / PySpark version
# =============================================================================
# CHANGES FROM THE ORIGINAL PURE-PYTHON SCRIPT (I/O ONLY — parsing / business
# logic is untouched):
#
#   • INPUT  : instead of reading INPUT_CSV off disk, this notebook expects a
#              Spark DataFrame called `input_df` to already exist in the
#              notebook context (e.g. produced by spark.table(...) or
#              spark.read.csv(...) in a previous cell).
#   • OUTPUT : instead of writing an .xlsx file, the final result is returned
#              as a Spark DataFrame (`usage_metrics_df`). No files are written
#              anywhere (no temp CSVs, no skip-log file, no Excel file).
#   • Because the raw text is now delivered as a Spark DataFrame (Spark has
#              already handled file encoding / multiline parsing at load
#              time), the source-encoding sniffing (_detect_source_encoding)
#              and the temp-CSV round trip (_write_temp_csv /
#              csv.field_size_limit) are no longer needed and have been
#              removed. Row-shape/format auto-detection (new_format vs
#              sql_users_format) and every parsing/aggregation function
#              (_normalize_date, extract_table_column_pairs, classify,
#              _strip_locking_prefix, _extract_inner_query, _extract_from_node,
#              the STEP 4 groupby aggregation, STEP 5 column ordering) are
#              copied over unchanged.
# =============================================================================

# COMMAND ----------

import re
import logging
from datetime import datetime

import pandas as pd
import sqlglot
from sqlglot import exp

# Silence sqlglot's benign "falling back to Command" warnings — we handle
# that fallback ourselves in extract_table_column_pairs() below, so the
# raw warning text would just be noise (one line per recovered query).
logging.getLogger("sqlglot").setLevel(logging.ERROR)

# COMMAND ----------

# ── CONFIG ────────────────────────────────────────────────────────────────────

APP_PREFIXES = ("svp", "ovt", "dt")        # case-insensitive prefix → app

# COMMAND ----------

# =============================================================================
# STEP 0 — Auto-detect input schema & normalize into (SqlTextInfo, Metric_Date,
#          users) records — in-memory, no temp files.
# =============================================================================
# Supports the same two input schemas as the original script:
#
# Format A — "new_format":
#   Columns: user_name, Db_nm, Tbl_nm, SqlTextInfo, LogDate, StartTime,
#            LastResponseTime
#   Detection: header contains "user_name" AND "logdate"
#
# Format B/C — "sql_users_format":
#   Columns: SqlTextInfo, Metric_Date, users
#
# Dates are normalized to YYYY-MM-DD (required by the STEP 1 validation),
# handling DD/MM/YYYY, YYYY/MM/DD, and ISO-8601 timestamps like
# 2026-06-25T14:48:39.466Z.
# =============================================================================


def _normalize_date(date_str: str) -> str:
    """
    Normalize a date string to YYYY-MM-DD. Handles:
      • YYYY-MM-DD                          -> unchanged
      • YYYY-MM-DDTHH:MM:SS(.ffffff)?Z?      -> date part only
                                                (2026-06-25T14:48:39.466Z -> 2026-06-25)
      • DD/MM/YYYY                           -> rearranged (29/02/2026 -> 2026-02-29)
      • YYYY/MM/DD                           -> rearranged
    Unrecognized shapes are returned unchanged.
    """
    date_str = date_str.strip()

    if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return date_str

    m = re.match(r'^(\d{4}-\d{2}-\d{2})[T ]\d{2}:\d{2}:\d{2}', date_str)
    if m:
        return m.group(1)

    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', date_str)
    if m:
        dd, mm, yyyy = m.groups()
        return f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}"

    m = re.match(r'^(\d{4})/(\d{1,2})/(\d{1,2})$', date_str)
    if m:
        yyyy, mm, dd = m.groups()
        return f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}"

    return date_str


def _normalize_sql_field(sql_raw: str) -> str:
    """Guarantee exactly one trailing ';'.

    (The original also escaped internal double-quotes so the field would
    round-trip safely through a temp CSV file. That round trip no longer
    happens here since we stay in-memory, so the escaping step — which
    existed purely to survive a CSV write/re-read — is not needed.)
    """
    sql_norm = sql_raw.strip().rstrip()
    if not sql_norm.endswith(";"):
        sql_norm += ";"
    return sql_norm


def _detect_format(columns) -> str:
    """
    Only two schemas need distinguishing up front:
      • "new_format"       — user_name/Db_nm/Tbl_nm/SqlTextInfo/LogDate/... columns
      • "sql_users_format" — SqlTextInfo,Metric_Date,users columns
    """
    cols_lower = {c.strip().lower() for c in columns}
    if "user_name" in cols_lower and "logdate" in cols_lower:
        return "new_format"
    return "sql_users_format"


def _convert_new_format_rows(pdf: pd.DataFrame):
    """Normalize a 'new_format' pandas DataFrame into (sql, date, user) tuples."""
    rows_out = []
    for row in pdf.itertuples(index=False):
        row_d = row._asdict()
        sql_raw  = str(row_d.get("SqlTextInfo") or "").strip()
        log_date = _normalize_date(str(row_d.get("LogDate") or "").strip())
        user     = str(row_d.get("user_name") or "").strip()

        if not sql_raw or not log_date or not user:
            continue

        rows_out.append((_normalize_sql_field(sql_raw), log_date, user))
    return rows_out


def _convert_sql_users_format_rows(pdf: pd.DataFrame):
    """Normalize a 'sql_users_format' pandas DataFrame into (sql, date, user) tuples."""
    rows_out = []
    for row in pdf.itertuples(index=False):
        row_d = row._asdict()
        sql_raw  = str(row_d.get("SqlTextInfo") or "").strip()
        log_date = _normalize_date(str(row_d.get("Metric_Date") or "").strip())
        user     = str(row_d.get("users") or "").strip()

        if not sql_raw or not log_date or not user:
            continue

        rows_out.append((_normalize_sql_field(sql_raw), log_date, user))
    return rows_out


# COMMAND ----------

# =============================================================================
# STEP 1 — Read from the Spark input DataFrame & validate rows
# =============================================================================
# `input_df` is expected to already exist in the notebook (e.g.
#   input_df = spark.table("catalog.schema.teradata_query_log")
# ), matching either the "new_format" or "sql_users_format" schema described
# above.
# =============================================================================

_fmt = _detect_format(input_df.columns)
print(f"[INFO] Detected input format: {_fmt}")

_input_pdf = input_df.toPandas()

if _fmt == "new_format":
    _normalized_rows = _convert_new_format_rows(_input_pdf)
else:
    _normalized_rows = _convert_sql_users_format_rows(_input_pdf)

print(f"[INFO] Normalized rows: {len(_normalized_rows)}")

raw_records = []
_skipped_bad_rows = 0

for sql_field, date_field, user_field in _normalized_rows:
    date_field = date_field.strip()

    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_field):
        _skipped_bad_rows += 1
        continue

    if not sql_field.strip() or not user_field.strip():
        _skipped_bad_rows += 1
        continue

    raw_records.append((sql_field, date_field, user_field))

print(f"[INFO] Records found : {len(raw_records)}")
if _skipped_bad_rows:
    print(f"[INFO] Rows skipped (malformed) : {_skipped_bad_rows}")

# COMMAND ----------

# =============================================================================
# STEP 2 — Helpers (unchanged from the original script)
# =============================================================================

def classify(username: str) -> str:
    return "app" if username.lower().startswith(APP_PREFIXES) else "user"


def _strip_locking_prefix(text: str) -> str:
    """
    Teradata locking-request modifiers (e.g. LOCKING ROW FOR ACCESS) are
    valid Teradata syntax at the TOP level of a statement, but sqlglot can't
    parse them when they appear nested inside a CREATE ... AS (...) body —
    that nesting is what causes the whole CREATE to fall back to a generic,
    unparseable "Command" node. Since the clause is just a lock hint (it
    doesn't affect which tables/columns are referenced), we strip it before
    re-parsing the inner query on its own.
    """
    stripped = text.lstrip()
    if re.match(r'(?i)^locking\b', stripped):
        m = re.search(r'(?i)\b(select|with)\b', stripped)
        if m:
            return stripped[m.start():]
    return text


def _extract_inner_query(sql_text: str):
    """
    Pull the SELECT/WITH body out of a CREATE ... AS (...) / CREATE ... AS
    SELECT ... statement so it can be parsed on its own, independent of the
    (possibly-unsupported-by-sqlglot) outer CREATE syntax.
    """
    m = re.search(r'\bAS\b\s*\(', sql_text, flags=re.IGNORECASE)
    if m:
        start = m.end() - 1  # index of '('
        depth = 0
        for i in range(start, len(sql_text)):
            if sql_text[i] == '(':
                depth += 1
            elif sql_text[i] == ')':
                depth -= 1
                if depth == 0:
                    return _strip_locking_prefix(sql_text[start + 1:i].strip())
        return None  # unbalanced parens — give up

    m = re.search(r'\bAS\b\s*(SELECT\b.*)', sql_text, flags=re.IGNORECASE | re.DOTALL)
    if m:
        return _strip_locking_prefix(m.group(1).strip())
    return None


def _extract_from_node(node) -> list:
    """Pull (table, column) pairs out of a parsed sqlglot node/subtree."""
    tables = [t.name.upper() for t in node.find_all(exp.Table) if t.name]
    cols   = list(dict.fromkeys(c.name.upper() for c in node.find_all(exp.Column) if c.name))
    stars  = list(node.find_all(exp.Star))

    pairs = []
    if stars and not cols:
        for tbl in tables:
            pairs.append((tbl, "*"))
    else:
        for tbl in tables:
            for col in cols:
                pairs.append((tbl, col))
    return pairs


def extract_table_column_pairs(raw_sql: str) -> tuple:
    """
    Returns (pairs, reason):
      pairs  — list of (TABLE, COLUMN) tuples, uppercased
      reason — None on success, string description if the SQL was unparseable

    Cases handled:
      CASE 1 — Genuine Teradata utility commands (SHOW/HELP/COLLECT/EXEC):
               sqlglot returns a Command node with no AST children.
               Logged & skipped, same as before.
      CASE 1b — CREATE ... AS (...) wrapping a LOCKING ROW FOR ACCESS (or
               similar) clause: sqlglot also returns Command for the WHOLE
               statement here, even though the inner query is perfectly
               valid Teradata SQL on its own. We recover by extracting the
               inner SELECT/WITH body, stripping the locking clause, and
               re-parsing just that — using ONLY the Teradata dialect (no
               cross-dialect fallback, to avoid other dialects silently
               "recovering" genuinely malformed SQL as something benign).
      CASE 2 — SELECT *: sqlglot returns Star nodes, not Column nodes →
               recorded as (TABLE, '*').
      CASE 3 — Normal SELECT/INSERT/UPDATE, and CREATE ... AS (SELECT ...)
               that parses fine directly → standard extraction, restricted
               to the query subtree (search_root) so the object being
               CREATEd isn't itself counted as a "referenced" table.
    """
    sql    = raw_sql.replace('""', '"').strip()
    sql    = re.sub(r'--[^\n]*', '', sql)       # strip  -- comments
    pairs  = []
    reason = None

    try:
        statements = sqlglot.parse(
            sql,
            dialect     = "teradata",
            error_level = sqlglot.ErrorLevel.IGNORE
        )
        for stmt in statements:
            if stmt is None:
                continue

            # CASE 1 / 1b: Teradata Command fallback
            if type(stmt).__name__ == "Command":
                inner_sql = _extract_inner_query(sql)
                recovered_pairs = []
                if inner_sql:
                    try:
                        inner_statements = sqlglot.parse(
                            inner_sql,
                            dialect     = "teradata",
                            error_level = sqlglot.ErrorLevel.IGNORE
                        )
                    except Exception:
                        inner_statements = []
                    if inner_statements and not any(
                        type(s).__name__ == "Command" for s in inner_statements if s is not None
                    ):
                        for inner_stmt in inner_statements:
                            if inner_stmt is not None:
                                recovered_pairs.extend(_extract_from_node(inner_stmt))

                if recovered_pairs:
                    pairs.extend(recovered_pairs)   # recovered — not a real skip
                else:
                    reason = f"Teradata utility command: {sql.strip()[:80]}"
                continue

            # search_root: for CREATE ... AS (SELECT ...), only look inside
            # the query part so the view/table name being CREATEd isn't
            # itself counted as a "referenced" table.
            search_root = stmt
            if isinstance(stmt, exp.Create):
                inner_expr = stmt.args.get("expression")
                if inner_expr is not None:
                    search_root = inner_expr

            pairs.extend(_extract_from_node(search_root))

    except Exception as exc:
        reason = f"Parse error: {exc}"

    if pairs:
        reason = None

    return pairs, reason


# COMMAND ----------

# =============================================================================
# STEP 3 — Explode every record → flat rows (unchanged from the original)
# =============================================================================

exploded = []
skip_log = []
_total_records = len(raw_records)
_progress_every = 20_000  # print a heartbeat every N records on large runs

for _i, (raw_sql, date, user) in enumerate(raw_records, start=1):
    date = date.strip()
    user = user.strip()
    acct = classify(user)

    pairs, reason = extract_table_column_pairs(raw_sql)

    if reason:
        skip_log.append({"date": date, "user": user, "reason": reason,
                         "sql": raw_sql.replace('""', '"').strip()[:300]})

    for tbl, col in pairs:
        exploded.append({
            "Log_Date"   : date,
            "Table_Name" : tbl,
            "Column_Name": col,
            "username"   : user,
            "acct_type"  : acct,
        })

    if _i % _progress_every == 0 or _i == _total_records:
        print(f"[INFO] Parsed {_i:,} / {_total_records:,} records "
              f"({len(exploded):,} rows exploded so far)")

print(f"[INFO] Exploded rows (before agg) : {len(exploded)}")
print(f"[INFO] Skipped / unparseable      : {len(skip_log)}")

if not exploded:
    raise ValueError("[ERROR] No rows after parsing — check input_df contents and format.")

df_exp = pd.DataFrame(exploded)

# COMMAND ----------

# =============================================================================
# STEP 4 — Aggregate by (Log_Date, Table_Name, Column_Name)
# (unchanged from the original)
# =============================================================================

GRP_KEYS = ["Log_Date", "Table_Name", "Column_Name"]

df_users = (df_exp[df_exp["acct_type"] == "user"]
            .groupby(GRP_KEYS)["username"]
            .nunique()
            .rename("Distinct_Users"))

df_apps  = (df_exp[df_exp["acct_type"] == "app"]
            .groupby(GRP_KEYS)["username"]
            .nunique()
            .rename("Distinct_Apps"))

df_count = (df_exp.groupby(GRP_KEYS)["username"]
            .count()
            .rename("Usage_Count"))

df_agg = (pd.concat([df_count, df_users, df_apps], axis=1)
            .fillna(0)
            .astype({"Distinct_Users": int, "Distinct_Apps": int})
            .reset_index()
            .sort_values(GRP_KEYS)
            .reset_index(drop=True))

# COMMAND ----------

# =============================================================================
# STEP 5 — Add Row_Wid and Created_Timestamp (unchanged from the original)
# =============================================================================

df_agg.insert(0, "Row_Wid", range(1, len(df_agg) + 1))
df_agg["Created_Timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

df_final = df_agg[[
    "Row_Wid", "Log_Date", "Table_Name", "Column_Name",
    "Usage_Count", "Distinct_Users", "Distinct_Apps", "Created_Timestamp",
]]

print(f"[INFO] Final aggregated rows      : {len(df_final)}")
print(df_final.head(10).to_string(index=False))

# COMMAND ----------

# =============================================================================
# STEP 6 — Skip log (kept in memory only — no file is written)
# =============================================================================

if skip_log:
    print(f"[INFO] {len(skip_log)} queries were skipped/unparseable. "
          f"First few reasons:")
    for entry in skip_log[:5]:
        print(f"  date={entry['date']}  user={entry['user']}  reason={entry['reason']}")

# Optional: expose the skip log as a Spark DataFrame too, purely in-memory
# (comment out if not needed).
skip_log_df = spark.createDataFrame(pd.DataFrame(skip_log)) if skip_log else None

# COMMAND ----------

# =============================================================================
# STEP 7 — Convert final pandas DataFrame to a Spark DataFrame (output)
# =============================================================================
# Replaces the original openpyxl Excel-writing step. No files are written —
# `usage_metrics_df` is the pipeline's output Spark DataFrame.
# =============================================================================

usage_metrics_df = spark.createDataFrame(df_final)

display(usage_metrics_df)

# COMMAND ----------

# usage_metrics_df is now available for downstream use, e.g.:
#   usage_metrics_df.write.mode("overwrite").saveAsTable("catalog.schema.teradata_usage_metrics")
