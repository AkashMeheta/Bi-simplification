# Databricks notebook cell -- self-contained, no import needed.
#
# Returns ONE row per query: the MAIN table (the top-level FROM table),
# ignoring JOINs, subqueries, and CTEs.
#
# HOW TO USE:
#   1. Set `input_df` below to your DataFrame (e.g. input_df = _sqldf)
#   2. Set `sql_col` to the column name holding the raw SQL text
#      (defaults to "sqltextinfo")
#   3. Run the cell. The result is in `result_df` -- display(result_df)

import re
import sqlglot
from sqlglot import exp

from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

# ============================================================
# 1) SET YOUR INPUT HERE
# ============================================================
input_df = _sqldf                 # <-- point this at your DataFrame
sql_col = "sqltextinfo"           # <-- column name holding the raw SQL text
sql_dialect = "databricks"        # use "teradata" if parsing Teradata query logs

# ============================================================
# 2) PARSING LOGIC (no need to edit below this line)
# ============================================================

# Matches redaction/masking placeholders like <REDACTED>, <PII>, <MASKED VALUE>
# that would otherwise be mis-parsed as a "<" (less-than) operator with
# nothing on the right-hand side.
_REDACTION_PATTERN = re.compile(r"<[A-Za-z0-9_ ]+>")


def _clean_sql(sql_text):
    return _REDACTION_PATTERN.sub("'REDACTED'", sql_text)


def _main_select(stmt):
    """Unwrap UNION/UNION ALL to get the first underlying SELECT, since
    the main table of a UNION query is the FROM table of its first branch."""
    node = stmt
    while isinstance(node, exp.Union):
        node = node.this
    return node


def _get_main_table(sql_text):
    """
    Return a dict describing ONLY the top-level FROM table of the query --
    not JOINed tables, not tables inside subqueries, not CTEs. If the main
    FROM source is itself a subquery or a CTE alias (not a real physical
    table), returns a dict with parse_error explaining why there's no
    single physical "main table".
    """
    if not sql_text or not sql_text.strip():
        return {
            "catalog": None, "database": None, "table_name": None,
            "full_table_name": None, "alias": None,
            "parse_error": "Empty SQL text",
        }

    cleaned = _clean_sql(sql_text)

    try:
        stmt = sqlglot.parse_one(cleaned, read=sql_dialect)
    except Exception as e1:
        try:
            stmt = sqlglot.parse_one(cleaned)  # fallback: generic dialect
        except Exception as e2:
            return {
                "catalog": None, "database": None, "table_name": None,
                "full_table_name": None, "alias": None,
                "parse_error": f"{type(e2).__name__}: {e2}",
            }

    node = _main_select(stmt)

    # CTE names defined on this statement -- if the main FROM table is
    # actually a reference to one of these, it's not a physical table.
    cte_names = {c.alias.lower() for c in node.find_all(exp.CTE) if c.alias}

    from_clause = node.args.get("from_") or node.args.get("from")
    if from_clause is None:
        return {
            "catalog": None, "database": None, "table_name": None,
            "full_table_name": None, "alias": None,
            "parse_error": "No top-level FROM clause found (e.g. not a SELECT statement)",
        }

    table_expr = from_clause.this

    if not isinstance(table_expr, exp.Table):
        return {
            "catalog": None, "database": None, "table_name": None,
            "full_table_name": None, "alias": None,
            "parse_error": "Main FROM source is a derived subquery, not a physical table",
        }

    table_name = table_expr.name
    if table_name and table_name.lower() in cte_names:
        return {
            "catalog": None, "database": None, "table_name": table_name,
            "full_table_name": None, "alias": table_expr.alias or None,
            "parse_error": "Main FROM source is a CTE alias, not a physical table",
        }

    catalog = table_expr.catalog or None
    database = table_expr.db or None
    alias = table_expr.alias or None
    parts = [p for p in (catalog, database, table_name) if p]

    return {
        "catalog": catalog,
        "database": database,
        "table_name": table_name,
        "full_table_name": ".".join(parts),
        "alias": alias,
        "parse_error": None,
    }


RESULT_SCHEMA = StructType(
    [
        StructField("catalog", StringType(), True),
        StructField("database", StringType(), True),
        StructField("table_name", StringType(), True),
        StructField("full_table_name", StringType(), True),
        StructField("alias", StringType(), True),
        StructField("parse_error", StringType(), True),
    ]
)

_parse_udf = F.udf(_get_main_table, RESULT_SCHEMA)

# ============================================================
# 3) RUN
# ============================================================

_with_id = input_df.withColumn("query_id", F.monotonically_increasing_id())
_with_main = _with_id.withColumn("_main", _parse_udf(F.col(sql_col)))

result_df = _with_main.select(
    F.col("query_id"),
    F.col(sql_col).alias("sql_text"),
    F.col("_main.catalog").alias("catalog"),
    F.col("_main.database").alias("database"),
    F.col("_main.table_name").alias("table_name"),
    F.col("_main.full_table_name").alias("full_table_name"),
    F.col("_main.alias").alias("alias"),
    F.col("_main.parse_error").alias("parse_error"),
)

display(result_df)

print("\nParse status summary:")
result_df.groupBy(F.col("parse_error").isNull().alias("parsed_ok")).count().show()
