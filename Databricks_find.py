# DEBUG CELL -- run this first to see WHY every row is coming back null.
# Paste into one Databricks cell, set input_df / sql_col, run it.

import sqlglot
from sqlglot import exp
from pyspark.sql import functions as F

input_df = _sqldf
sql_col = "sqltextinfo"
sql_dialect = "databricks"

# ---- Step 1: confirm the column exists and is actually a string ----
print("Schema:")
input_df.printSchema()

# ---- Step 2: pull a few raw values out and print them exactly as-is ----
print("\nSample raw values:")
sample_rows = input_df.select(sql_col).limit(3).collect()
for i, row in enumerate(sample_rows):
    val = row[sql_col]
    print(f"\n--- row {i} (type={type(val)}) ---")
    print(repr(val)[:500])

# ---- Step 3: try parsing those same values directly with sqlglot,
#      OUTSIDE of a UDF, so exceptions aren't swallowed ----
print("\nDirect sqlglot parse attempts:")
for i, row in enumerate(sample_rows):
    val = row[sql_col]
    if val is None:
        print(f"row {i}: value is None -- column itself is empty for this row")
        continue
    try:
        statements = sqlglot.parse(val, read=sql_dialect)
        tables = []
        for stmt in statements:
            if stmt is None:
                continue
            for t in stmt.find_all(exp.Table):
                tables.append((t.catalog, t.db, t.name))
        print(f"row {i}: OK -- found {len(tables)} table refs -> {tables}")
    except Exception as e:
        print(f"row {i}: FAILED -- {type(e).__name__}: {e}")

# ---- Step 4: check whether sqlglot is even importable on the executors
#      (not just the driver) -- this is the #1 cause of "everything null"
#      on shared/serverless Unity Catalog clusters, since %pip installs
#      on the driver don't always propagate to executors automatically ----
def _check_worker():
    import sqlglot
    return sqlglot.__version__

try:
    worker_version = input_df.rdd.mapPartitions(lambda _: [_check_worker()]).first()
    print(f"\nsqlglot version on executor: {worker_version}")
except Exception as e:
    print(f"\nsqlglot is NOT available on executors -- this is likely the cause: {type(e).__name__}: {e}")
