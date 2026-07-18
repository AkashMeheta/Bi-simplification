# extract_sql_tables.py
#
# Parses a column of raw SQL text (e.g. the `sqltextinfo` column produced by
# query-history / _sqldf style DataFrames) and extracts every referenced
# catalog / database / table into a flat Spark DataFrame -- one row per
# table reference per query, so a single query with 5 JOINs produces 5 rows.
#
# Handles complex queries: CTEs, subqueries, inline comments, multi-part
# names (catalog.schema.table), UNION/UNION ALL, INSERT/MERGE/CREATE, etc.
# Uses sqlglot (same library already used in the Teradata usage/join
# pipelines) with the "databricks" dialect since this is a Unity Catalog
# workspace. Change `SQL_DIALECT` below if you're parsing Teradata SQL
# instead (use "teradata").
#
# Usage (inside a Databricks notebook):
#   from extract_sql_tables import extract_tables_df
#   result_df = extract_tables_df(input_df, sql_col="sqltextinfo")
#   display(result_df)

import sqlglot
from sqlglot import exp

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    ArrayType,
    StringType,
    StructField,
    StructType,
)

SQL_DIALECT = "databricks"  # use "teradata" if parsing Teradata query logs

# Schema for the array-of-structs the UDF returns: one struct per table
# reference found in a query.
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


def _parse_sql_tables(sql_text: str):
    """
    Parse a single SQL string and return a list of dicts describing every
    table reference in it (FROM, JOIN, subqueries, CTEs' underlying source
    tables, INSERT/MERGE targets, etc).

    Returns an empty list (never raises) if the SQL can't be parsed, so a
    handful of malformed rows don't blow up the whole job -- mirrors the
    skipped_queries.txt approach used in the Teradata pipelines, except here
    the "skip" signal is just an empty array, which you can filter on.
    """
    if not sql_text or not sql_text.strip():
        return []

    try:
        statements = sqlglot.parse(sql_text, read=SQL_DIALECT)
    except Exception:
        try:
            # Some dialect-specific syntax can fail; fall back to generic
            # ANSI parsing as a second attempt before giving up.
            statements = sqlglot.parse(sql_text)
        except Exception:
            return []

    # Names introduced by CTEs (WITH x AS (...)) aren't real tables --
    # they're aliases for a subquery -- so we exclude them from the result.
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
            if not table_name:
                continue
            if table_name.lower() in cte_names:
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


def extract_tables_df(
    df: DataFrame,
    sql_col: str = "sqltextinfo",
    id_col: str = None,
) -> DataFrame:
    """
    Given a Spark DataFrame containing a column of raw SQL text, return a
    new DataFrame with one row per (query, table reference), containing the
    catalog, database, and table name pulled out of each query.

    Parameters
    ----------
    df : DataFrame
        Input DataFrame, e.g. your `_sqldf` result with a `sqltextinfo` col.
    sql_col : str
        Name of the column holding the raw SQL text. Default "sqltextinfo".
    id_col : str, optional
        Name of an existing unique-id column in `df` to carry through as
        `query_id`. If None, a row number is generated instead so every
        query in the output can still be traced back to its source row.

    Returns
    -------
    DataFrame with columns:
        query_id, sql_text, catalog, database, table_name,
        full_table_name, alias
    """
    parse_udf = F.udf(_parse_sql_tables, TABLE_REF_SCHEMA)

    if id_col is None:
        base = df.withColumn("query_id", F.monotonically_increasing_id())
        id_col = "query_id"
    else:
        base = df.withColumnRenamed(id_col, "query_id") if id_col != "query_id" else df

    with_refs = base.withColumn("_table_refs", parse_udf(F.col(sql_col)))

    exploded = with_refs.withColumn("_ref", F.explode_outer(F.col("_table_refs")))

    result = exploded.select(
        F.col("query_id"),
        F.col(sql_col).alias("sql_text"),
        F.col("_ref.catalog").alias("catalog"),
        F.col("_ref.database").alias("database"),
        F.col("_ref.table_name").alias("table_name"),
        F.col("_ref.full_table_name").alias("full_table_name"),
        F.col("_ref.alias").alias("alias"),
    )

    return result


if __name__ == "__main__":
    # Small smoke test you can run standalone (spark-submit or a notebook
    # cell) to sanity check the parsing logic against a query shaped like
    # the one in the screenshot.
    spark = SparkSession.builder.getOrCreate()

    sample_sql = """
    SELECT COUNT(DISTINCT kpi.src_cust_id)
    FROM usm_prod.ccw_clm_pubz.clm_save_oppor_kpi_incrmtl_sv AS kpi
    INNER JOIN usm_prod.ccw_pubz.CLM_LN_PHRM_EXT_MV AS ext_mv
        ON kpi.PBM_CNTRCT_ID = ext_mv.PBM_CNTRCT_ID
    INNER JOIN usm_prod.ccw_pubz.CLM_LN_PHRM_MV AS phrm_mv
        ON ext_mv.CLM_SYS_CLM_ID = phrm_mv.CLM_SYS_CLM_ID
    INNER JOIN usm_prod.ccw_pubz.pbm_cust_dtl AS pbm
        ON LTRIM(pbm.SRC_CUST_ID) = LTRIM(kpi.src_cust_id)
    WHERE kpi.src_cust_id IN (
        SELECT DISTINCT a.src_cust_id FROM usm_dev.ccw.some_table a
    )
    """

    sample_df = spark.createDataFrame(
        [(1, sample_sql), (2, "SELECT * FROM usm_prod.ccw.orders o JOIN usm_prod.ccw.customers c ON o.cust_id = c.id")],
        ["row_num", "sqltextinfo"],
    )

    out = extract_tables_df(sample_df, sql_col="sqltextinfo", id_col="row_num")
    out.show(truncate=False)
