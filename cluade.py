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

# 1. Read everything as STRING first — never let spark infer timestamp on read
raw_df = spark.read.csv(
    "/path/to/your.csv",
    header=True,
    inferSchema=False,     # force everything to string
    multiLine=True,        # in case sqlQueryTxt spans multiple lines
    escape='"',
    quote='"'
)

# Force-cast known columns to string just to be safe
for c in ["starttime", "LastResponseTime"]:
    raw_df = raw_df.withColumn(c, F.col(c).cast(StringType()))

# 2. Define all the timestamp formats you've actually seen in the data
#    (add/remove based on what your CSV sources actually contain)
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

def normalize_timestamp_col(df, colname):
    """
    Try each format in order; first non-null match wins.
    Blank/null/unparseable -> stays null (don't fabricate a value).
    """
    trimmed = F.trim(F.col(colname))
    # Treat empty string as null upfront
    cleaned = F.when(trimmed == "", None).otherwise(trimmed)

    attempts = [F.to_timestamp(cleaned, fmt) for fmt in TIMESTAMP_FORMATS]
    normalized = F.coalesce(*attempts)

    return df.withColumn(colname + "_ts", normalized)

# 3. Apply to both timestamp columns
df = normalize_timestamp_col(raw_df, "starttime")
df = normalize_timestamp_col(raw_df, "LastResponseTime")

# 4. Optional: flag rows where parsing failed but original value wasn't blank
#    (useful for a rejects/audit log, similar to your skipped_queries.txt pattern)
df = df.withColumn(
    "starttime_parse_failed",
    (F.col("starttime").isNotNull()) & (F.trim(F.col("starttime")) != "") & (F.col("starttime_ts").isNull())
).withColumn(
    "LastResponseTime_parse_failed",
    (F.col("LastResponseTime").isNotNull()) & (F.trim(F.col("LastResponseTime")) != "") & (F.col("LastResponseTime_ts").isNull())
)

# 5. Swap in the real timestamp columns and drop the raw strings + helper cols
final_df = (
    df.drop("starttime", "LastResponseTime")
      .withColumnRenamed("starttime_ts", "starttime")
      .withColumnRenamed("LastResponseTime_ts", "LastResponseTime")
)

# 6. Write to Delta / Unity Catalog table with proper timestamp types
final_df.write.mode("overwrite").format("delta").saveAsTable("your_catalog.your_schema.your_table")


_______


WITH query_counts AS (

    SELECT 

        LogDate,

        SqlTextInfo,

        COUNT(*) AS usage_count

    FROM your_table

    GROUP BY LogDate, SqlTextInfo

),

ranked_queries AS (

    SELECT *,

           ROW_NUMBER() OVER (PARTITION BY LogDate ORDER BY usage_count DESC) AS rn

    FROM query_counts

)

SELECT 

    LogDate,

    SqlTextInfo AS most_used_query,

    usage_count

FROM ranked_queries

WHERE rn = 1

ORDER BY LogDate DESC;
