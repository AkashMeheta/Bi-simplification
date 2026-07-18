# Databricks notebook cell -- self-contained, no import needed.
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
from pyspark.sql.types import ArrayType, StringType, StructField, StructType

# ============================================================
# 1) SET YOUR INPUT HERE
# ============================================================
input_df = _sqldf                 # <-- point this at your DataFrame
sql_col = "sqltextinfo"           # <-- column name holding the raw SQL text
sql_dialect = "databricks"        # use "teradata" if parsing Teradata query logs

# ============================================================
# 2) PARSING LOGIC (no need to edit below this line)
# ============================================================

TABLE_REF_SCHEMA = ArrayType(
    StructType(
        [
            StructField("catalog", StringType(), True),
            StructField("database", StringType(), True),
            StructField("table_name", StringType(), True),
            StructField("full_table_name", StringType(), True),
            StructField("alias", StringType(), True),
        ]
    )
)

# Matches redaction/masking placeholders like <REDACTED>, <PII>, <MASKED VALUE>
# that show up inside literal positions (e.g. LTRIM(col, <REDACTED>)) and
# would otherwise be mis-parsed as a "<" (less-than) operator with nothing
# on the right-hand side. Swapping them for a harmless quoted string keeps
# the surrounding SQL syntactically valid without changing which tables
# are referenced.
_REDACTION_PATTERN = re.compile(r"<[A-Za-z0-9_ ]+>")


def _clean_sql(sql_text):
    return _REDACTION_PATTERN.sub("'REDACTED'", sql_text)


def _parse_sql_tables(sql_text):
    """
    Parse one SQL string and return a list of dicts, one per table
    reference found anywhere in the query (FROM, JOINs, subqueries,
    WHERE ... IN (...), etc). CTE aliases are excluded since they're
    not real tables.

    Every dict also carries `parse_error` (None on success) so failures
    are visible in the output instead of silently becoming nulls.
    """
    if not sql_text or not sql_text.strip():
        return []

    cleaned = _clean_sql(sql_text)

    try:
        statements = sqlglot.parse(cleaned, read=sql_dialect)
    except Exception as e1:
        try:
            statements = sqlglot.parse(cleaned)  # fallback: generic dialect
        except Exception as e2:
            return [
                {
                    "catalog": None,
                    "database": None,
                    "table_name": None,
                    "full_table_name": None,
                    "alias": None,
                    "parse_error": f"{type(e2).__name__}: {e2}",
                }
            ]

    cte_names = set()
    refs = []
    seen = set()

    for stmt in statements:
        if stmt is None:
            continue

        for cte in stmt.find_all(exp.CTE):
            if cte.alias:
                cte_names.add(cte.alias.lower())

        for table in stmt.find_all(exp.Table):
            table_name = table.name
            if not table_name or table_name.lower() in cte_names:
                continue

            catalog = table.catalog or None
            database = table.db or None
            alias = table.alias or None

            parts = [p for p in (catalog, database, table_name) if p]
            full_name = ".".join(parts)

            dedup_key = (catalog, database, table_name, alias)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            refs.append(
                {
                    "catalog": catalog,
                    "database": database,
                    "table_name": table_name,
                    "full_table_name": full_name,
                    "alias": alias,
                    "parse_error": None,
                }
            )

    if not refs:
        refs.append(
            {
                "catalog": None,
                "database": None,
                "table_name": None,
                "full_table_name": None,
                "alias": None,
                "parse_error": "No table references found (query parsed OK but had 0 tables)",
            }
        )

    return refs


TABLE_REF_SCHEMA_WITH_ERROR = ArrayType(
    StructType(
        [
            StructField("catalog", StringType(), True),
            StructField("database", StringType(), True),
            StructField("table_name", StringType(), True),
            StructField("full_table_name", StringType(), True),
            StructField("alias", StringType(), True),
            StructField("parse_error", StringType(), True),
        ]
    )
)

_parse_udf = F.udf(_parse_sql_tables, TABLE_REF_SCHEMA_WITH_ERROR)

# ============================================================
# 3) RUN
# ============================================================

_with_id = input_df.withColumn("query_id", F.monotonically_increasing_id())
_with_refs = _with_id.withColumn("_table_refs", _parse_udf(F.col(sql_col)))
_exploded = _with_refs.withColumn("_ref", F.explode_outer(F.col("_table_refs")))

result_df = _exploded.select(
    F.col("query_id"),
    F.col(sql_col).alias("sql_text"),
    F.col("_ref.catalog").alias("catalog"),
    F.col("_ref.database").alias("database"),
    F.col("_ref.table_name").alias("table_name"),
    F.col("_ref.full_table_name").alias("full_table_name"),
    F.col("_ref.alias").alias("alias"),
    F.col("_ref.parse_error").alias("parse_error"),
)

display(result_df)

# Quick summary so you can see success/failure counts at a glance
print("\nParse status summary:")
result_df.groupBy(F.col("parse_error").isNull().alias("parsed_ok")).count().show()
