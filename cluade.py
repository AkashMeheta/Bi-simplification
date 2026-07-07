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
