# Databricks notebook cell -- self-contained, no import needed.
#
# HOW TO USE:
#   1. Set `input_df` below to your DataFrame (e.g. input_df = _sqldf)
#   2. Set `sql_col` to the column name holding the raw SQL text
#      (defaults to "sqltextinfo")
#   3. Run the cell. The result is in `result_df` -- display(result_df)
#
# Everything (imports, UDF, function) lives in this one cell, so there's
# nothing to install or import from another file.

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


def _parse_sql_tables(sql_text):
    """
    Parse one SQL string and return a list of dicts, one per table
    reference found anywhere in the query (FROM, JOINs, subqueries,
    WHERE ... IN (...), etc). CTE aliases are excluded since they're
    not real tables. Never raises -- returns [] on unparseable SQL so
    one bad row doesn't fail the whole job.
    """
    if not sql_text or not sql_text.strip():
        return []

    try:
        statements = sqlglot.parse(sql_text, read=sql_dialect)
    except Exception:
        try:
            statements = sqlglot.parse(sql_text)
        except Exception:
            return []

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
                }
            )

    return refs


_parse_udf = F.udf(_parse_sql_tables, TABLE_REF_SCHEMA)

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
)

display(result_df)
