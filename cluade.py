SELECT 
    SqlTextInfo,
    MAX(unix_timestamp(LastResponseTime) - unix_timestamp(StartTime)) AS execution_time,
    MAX(LogDate) AS last_used_date
FROM your_table
WHERE LastResponseTime IS NOT NULL 
  AND StartTime IS NOT NULL
GROUP BY SqlTextInfo
ORDER BY execution_time DESC, last_used_date DESC
LIMIT 10;

SELECT 
    SqlTextInfo,
    usage_count,
    last_used_date
FROM (
    SELECT 
        SqlTextInfo,
        COUNT(*) AS usage_count,
        MAX(LogDate) AS last_used_date,
        ROW_NUMBER() OVER (
            ORDER BY COUNT(*) DESC, MAX(LogDate) DESC
        ) AS rn
    FROM your_table
    GROUP BY SqlTextInfo
) t
WHERE rn <= 10;


from pyspark.sql import SparkSession
from pyspark.sql.functions import to_timestamp, coalesce, col, unix_timestamp, when

spark = SparkSession.builder.getOrCreate()

# 🔹 1. Read table
df = spark.table("your_table")

# 🔹 2. Parse timestamps safely (handle multiple formats)
df = df.withColumn(
    "start_time",
    coalesce(
        to_timestamp("StartTime", "yyyy-MM-dd'T'HH:mm:ss.SSSXXX"),
        to_timestamp("StartTime", "yyyy-MM-dd HH:mm:ss.SSSSSS"),
        to_timestamp("StartTime", "yyyy-MM-dd HH:mm:ss")
    )
).withColumn(
    "end_time",
    coalesce(
        to_timestamp("LastResponseTime", "yyyy-MM-dd'T'HH:mm:ss.SSSXXX"),
        to_timestamp("LastResponseTime", "yyyy-MM-dd HH:mm:ss.SSSSSS"),
        to_timestamp("LastResponseTime", "yyyy-MM-dd HH:mm:ss")
    )
)

# 🔹 3. Calculate execution time (in seconds)
df = df.withColumn(
    "total_execution_time",
    when(
        (col("start_time").isNotNull()) &
        (col("end_time").isNotNull()) &
        (unix_timestamp("end_time") >= unix_timestamp("start_time")),
        unix_timestamp("end_time") - unix_timestamp("start_time")
    ).otherwise(None)
)

# 🔹 4. Drop temp columns (optional)
df = df.drop("start_time", "end_time")

# 🔹 5. Overwrite table with new column
df.write.mode("overwrite").option("overwriteSchema", "

____45


from pyspark.sql import functions as F
from pyspark.sql.types import StringType

# 1. Read everything as STRING — never let Spark infer timestamp on read
raw_df = spark.read.csv(
    "/path/to/your.csv",
    header=True,
    inferSchema=False,
    multiLine=True,
    escape='"',
    quote='"'
)

# 2. Resolve actual column names case-insensitively (fixes the StartTime/starttime mismatch)
def resolve_col(df, expected_name):
    matches = [c for c in df.columns if c.lower() == expected_name.lower()]
    if not matches:
        raise ValueError(f"Column '{expected_name}' not found. Available columns: {df.columns}")
    return matches[0]

col_logdate       = resolve_col(raw_df, "LogDate")
col_sqltxt        = resolve_col(raw_df, "sqlQueryTxt")
col_starttime     = resolve_col(raw_df, "starttime")
col_lastresponse  = resolve_col(raw_df, "LastResponseTime")

# 3. Timestamp formats seen across sources
TIMESTAMP_FORMATS = [
    "yyyy-MM-dd HH:mm:ss.SSSSSS",
    "yyyy-MM-dd HH:mm:ss.SSS",
    "yyyy-MM-dd HH:mm:ss",
    "yyyy-MM-dd'T'HH:mm:ss.SSSSSS",
    "yyyy-MM-dd'T'HH:mm:ss",
    "MM/dd/yyyy HH:mm:ss",
    "dd/MM/yyyy HH:mm:ss",
    "MM-dd-yyyy HH:mm:ss",
    "yyyy/MM/dd HH:mm:ss",
]

def build_timestamp_expr(colname):
    """Returns a single coalesce expression trying every format, without any withColumn loop."""
    trimmed = F.trim(F.col(colname))
    cleaned = F.when(trimmed == "", None).otherwise(trimmed)
    attempts = [F.to_timestamp(cleaned, fmt) for fmt in TIMESTAMP_FORMATS]
    return F.coalesce(*attempts)

# 4. Build every output column expression up front (list comprehension, not a withColumn loop)
starttime_ts_expr    = build_timestamp_expr(col_starttime)
lastresponse_ts_expr = build_timestamp_expr(col_lastresponse)

select_exprs = [
    F.col(col_logdate).alias("LogDate"),
    F.col(col_sqltxt).alias("sqlQueryTxt"),
    starttime_ts_expr.alias("starttime"),
    lastresponse_ts_expr.alias("LastResponseTime"),
    # Audit flags for rows where the raw value was non-blank but still failed to parse
    (
        F.col(col_starttime).isNotNull()
        & (F.trim(F.col(col_starttime)) != "")
        & starttime_ts_expr.isNull()
    ).alias("starttime_parse_failed"),
    (
        F.col(col_lastresponse).isNotNull()
        & (F.trim(F.col(col_lastresponse)) != "")
        & lastresponse_ts_expr.isNull()
    ).alias("lastresponsetime_parse_failed"),
]

# 5. Single select — one projection, one pass, Catalyst optimizes it as a whole
final_df = raw_df.select(*select_exprs)

# 6. Write to Delta / Unity Catalog with proper timestamp types
final_df.write.mode("overwrite").format("delta").saveAsTable("your_catalog.your_schema.your_table")
