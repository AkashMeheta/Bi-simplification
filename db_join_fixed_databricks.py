# Databricks notebook source
# =============================================================================
# Teradata Query JOIN Metrics Pipeline — Databricks / PySpark version
# =============================================================================
# CHANGES FROM THE ORIGINAL PURE-PYTHON SCRIPT (I/O ONLY — parsing / business
# logic is untouched):
#
#   • INPUT  : instead of reading INPUT_CSV off disk, this notebook expects a
#              Spark DataFrame called `input_df` to already exist in the
#              notebook context (e.g. produced by spark.table(...) or
#              spark.read.csv(...) in a previous cell).
#   • OUTPUT : instead of writing an .xlsx file, the final result is returned
#              as a Spark DataFrame (`join_metrics_df`). No files are written
#              anywhere (no temp CSVs, no skip-log file, no Excel file).
#   • Because the raw text is now delivered as a Spark DataFrame (Spark has
#              already handled file encoding / multiline parsing at load
#              time), the source-encoding sniffing (_detect_source_encoding)
#              and the temp-CSV round trip (_write_temp_csv /
#              csv.field_size_limit) are no longer needed and have been
#              removed. Row-shape/format auto-detection (new_format vs
#              sql_users_format) and every parsing/aggregation function
#              (_normalize_date, extract_joins, extract_join_keys,
#              extract_right_table_columns, classify, the STEP 4 groupby
#              aggregation, STEP 5 renaming) are copied over unchanged.
# =============================================================================

# COMMAND ----------

import re
from datetime import datetime

import pandas as pd
import sqlglot
from sqlglot import exp
from pyspark.sql import functions as F

# COMMAND ----------

# ── CONFIG ────────────────────────────────────────────────────────────────────

# Add any app/service account prefixes here (case-insensitive)
APP_PREFIXES = (
    "svp", "ovt", "dt",
    "svt",                  # SVT_ETL_SIARX_PRD style accounts
    "etl", "svc", "app",    # common service account patterns
)

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
    """'app' if username starts with any known app prefix, else 'user'."""
    return "app" if username.lower().startswith(APP_PREFIXES) else "user"


def get_db_and_table(tbl_node) -> tuple:
    """Extract (DATABASE, TABLE_NAME) from a sqlglot Table AST node."""
    db   = tbl_node.args.get("db")
    name = tbl_node.name
    return (
        db.name.upper()   if db   else "",
        name.upper()      if name else "",
    )


def resolve_join_table(join_this):
    """
    Return the physical Table node for the right-hand side of a JOIN.

    • Direct table:  JOIN db.tbl t  → returns the Table node
    • Subquery:      JOIN (SELECT ... FROM db.tbl) t
                     → resolves to the subquery's own FROM table so we always
                        record real table names, never anonymous subqueries.
    """
    if isinstance(join_this, exp.Table):
        return join_this

    if isinstance(join_this, exp.Subquery):
        inner = join_this.this
        if isinstance(inner, exp.Select):
            from_node = inner.args.get("from_")
            if from_node and isinstance(from_node.this, exp.Table):
                return from_node.this

    return None


def extract_right_table_columns(select, alias: str) -> str:
    """
    Find every column in this SELECT scope (select list, WHERE, ON,
    GROUP BY, HAVING, QUALIFY, ORDER BY — find_all walks the whole
    subtree) that is qualified with the given table/alias, i.e. the
    columns actually "fetched"/referenced against the right-hand JOIN
    table.

    e.g.  SELECT a.id, b.name, b.amt FROM t1 a JOIN t2 b ON a.id=b.id
          alias="b"  →  "NAME, AMT, ID"  (any column referenced on b,
          including join keys, is included)

    Deduped, preserving first-seen order. Returns "" if no alias or
    no qualified columns are found (e.g. columns referenced without a
    table qualifier).
    """
    if not alias:
        return ""
    cols = []
    for col in select.find_all(exp.Column):
        tbl_node = col.args.get("table")
        tbl_name = tbl_node.name if tbl_node else None
        if tbl_name and tbl_name.upper() == alias.upper() and col.name:
            cols.append(col.name.upper())
    return ", ".join(dict.fromkeys(cols))


def extract_join_keys(on_expr) -> str:
    """
    Extract column names used as join keys from an ON expression.
    e.g.  a.id = b.id AND a.type = b.type  →  "ID, TYPE"
    Deduped, preserving first-seen order.
    """
    if on_expr is None:
        return ""
    keys = []
    for eq in on_expr.find_all(exp.EQ):
        for col in eq.find_all(exp.Column):
            if col.name:
                keys.append(col.name.upper())
    return ", ".join(dict.fromkeys(keys))


def extract_joins(raw_sql: str) -> tuple:
    """
    Parse one SQL string and return (join_list, skip_reason).

    join_list — list of dicts with keys:
        left_db, left_tbl, right_db, right_tbl, join_type, join_keys,
        right_columns

    skip_reason — None if OK, string if the SQL was unparseable/utility.

    Handles:
        • Simple JOINs, multiple JOINs per SELECT
        • Subquery JOINs  (resolves to subquery's own FROM table)
        • UNION queries   (walks every SELECT scope independently)
        • Multi-key ON    (a.id=b.id AND a.type=b.type)
        • Right-table column usage (any column referenced against the
          JOIN table's alias anywhere in that SELECT scope)
    """
    sql         = raw_sql.replace('""', '"').strip()
    sql         = re.sub(r'--[^\n]*', '', sql)      # strip line comments
    results     = []
    skip_reason = None

    try:
        statements = sqlglot.parse(
            sql,
            dialect     = "teradata",
            error_level = sqlglot.ErrorLevel.IGNORE,
        )

        for stmt in statements:
            if stmt is None:
                continue

            # Utility commands have no parseable structure
            if type(stmt).__name__ == "Command":
                skip_reason = f"Teradata utility command: {sql.strip()[:80]}"
                continue

            # Walk every SELECT scope — handles UNIONs and nested subqueries
            for select in stmt.find_all(exp.Select):
                from_node = select.args.get("from_")
                if not from_node or not isinstance(from_node.this, exp.Table):
                    continue

                left_db, left_tbl = get_db_and_table(from_node.this)
                if not left_tbl:
                    continue

                for join in select.args.get("joins") or []:
                    right_node = resolve_join_table(join.this)
                    if right_node is None:
                        continue

                    right_db, right_tbl = get_db_and_table(right_node)
                    if not right_tbl:
                        continue

                    join_type = str(join.args.get("kind") or "").upper() or "JOIN"
                    join_keys = extract_join_keys(join.args.get("on"))

                    # Alias actually used to qualify columns in the SQL
                    # (falls back to the real table name when no alias
                    # was assigned to the JOIN'd table/subquery).
                    right_alias = join.this.alias_or_name or right_tbl
                    right_columns = extract_right_table_columns(select, right_alias)

                    results.append({
                        "left_db"      : left_db,
                        "left_tbl"     : left_tbl,
                        "right_db"     : right_db,
                        "right_tbl"    : right_tbl,
                        "join_type"    : join_type,
                        "join_keys"    : join_keys,
                        "right_columns": right_columns,
                    })

    except Exception as exc:
        skip_reason = f"Parse error: {exc}"

    return results, skip_reason


# COMMAND ----------

# =============================================================================
# STEP 3 — Explode every record → flat join rows (unchanged from the original)
# =============================================================================

exploded = []
skip_log = []
_total_records = len(raw_records)
_progress_every = 20_000  # print a heartbeat every N records on large runs

for _i, (raw_sql, date, user) in enumerate(raw_records, start=1):
    date = date.strip()
    user = user.strip()
    acct = classify(user)

    joins, skip_reason = extract_joins(raw_sql)

    if skip_reason:
        skip_log.append({
            "date"  : date,
            "user"  : user,
            "reason": skip_reason,
            "sql"   : raw_sql.replace('""', '"').strip()[:300],
        })

    if not joins:
        continue

    for j in joins:
        exploded.append({
            "Log_Date"     : date,
            "left_db"      : j["left_db"],
            "left_tbl"     : j["left_tbl"],
            "right_db"     : j["right_db"],
            "right_tbl"    : j["right_tbl"],
            "join_type"    : j["join_type"],
            "join_keys"    : j["join_keys"],
            "right_columns": j["right_columns"],
            "username"     : user,
            "acct_type"    : acct,
        })

    if _i % _progress_every == 0 or _i == _total_records:
        print(f"[INFO] Parsed {_i:,} / {_total_records:,} records "
              f"({len(exploded):,} join rows so far)")

print(f"[INFO] Exploded join rows (before agg) : {len(exploded)}")
print(f"[INFO] Skipped / unparseable           : {len(skip_log)}")

if not exploded:
    raise ValueError("[ERROR] No join rows extracted. Check input_df contents and format.")

df_exp = pd.DataFrame(exploded)

# COMMAND ----------

# =============================================================================
# STEP 4 — Aggregate (unchanged from the original)
# =============================================================================
# Group by (Log_Date, left_db, left_tbl, right_db, right_tbl, join_type, join_keys)
# Compute: Join_Count, Distinct_Users, Distinct_Apps, Right_Table_Columns
#
# Uses three separate groupby passes (avoids the lambda-closure bug where
# every group reports distinct count = 1), plus a fourth pass that unions
# every right-table column string seen within a group (different queries
# hitting the same join pair may reference different column subsets).
# =============================================================================

GRP_KEYS = [
    "Log_Date",
    "left_db", "left_tbl",
    "right_db", "right_tbl",
    "join_type", "join_keys",
]

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
            .rename("Join_Count"))


def _union_columns(series: pd.Series) -> str:
    """Union all comma-separated column lists within a group, deduped,
    preserving first-seen order across rows."""
    seen = {}
    for val in series:
        if not val:
            continue
        for col in val.split(","):
            col = col.strip()
            if col:
                seen[col] = True
    return ", ".join(seen.keys())


df_right_cols = (df_exp.groupby(GRP_KEYS)["right_columns"]
                  .apply(_union_columns)
                  .rename("Right_Table_Columns"))

df_agg = (pd.concat([df_count, df_users, df_apps, df_right_cols], axis=1)
            .fillna({"Distinct_Users": 0, "Distinct_Apps": 0, "Right_Table_Columns": ""})
            .astype({"Distinct_Users": int, "Distinct_Apps": int})
            .reset_index()
            .sort_values(GRP_KEYS)
            .reset_index(drop=True))

# COMMAND ----------

# =============================================================================
# STEP 5 — Rename columns to final schema & add Row_Wid / Created_Timestamp
# (unchanged from the original)
# =============================================================================

df_agg.insert(0, "Row_Wid", range(1, len(df_agg) + 1))
df_agg["Created_Timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

df_final = df_agg.rename(columns={
    "left_db"  : "Left_Table_Database",
    "left_tbl" : "Left_Table_Name",
    "right_db" : "Right_Table_Database",
    "right_tbl": "Right_Table_Name",
    "join_type": "Join_Type",
    "join_keys": "Join_Keys",
})[[
    "Row_Wid",
    "Log_Date",
    "Left_Table_Database",
    "Left_Table_Name",
    "Right_Table_Database",
    "Right_Table_Name",
    "Right_Table_Columns",
    "Join_Type",
    "Join_Keys",
    "Join_Count",
    "Distinct_Users",
    "Distinct_Apps",
    "Created_Timestamp",
]]

print(f"[INFO] Final aggregated rows : {len(df_final)}")
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
# `join_metrics_df` is the pipeline's output Spark DataFrame.
# =============================================================================

join_metrics_df = spark.createDataFrame(df_final)

display(join_metrics_df)

# COMMAND ----------

# join_metrics_df is now available for downstream use, e.g.:
#   join_metrics_df.write.mode("overwrite").saveAsTable("catalog.schema.teradata_join_metrics")
