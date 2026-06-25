from pyspark.sql.functions import udf
from pyspark.sql.types import StringType

def clean_sql(sql):
    if not sql:
        return sql

    # fix escaped quotes
    sql = sql.replace('""', '"')

    # remove inner quotes only
    sql = sql.replace('"', '')

    return sql

clean_sql_udf = udf(clean_sql, StringType())

df_clean = df.withColumn("SqlTextInfo", clean_sql_udf(col("SqlTextInfo")))

-------------------------

from pyspark.sql import SparkSession
from pyspark.sql.functions import col, regexp_replace, when

spark = SparkSession.builder.appName("CleanSQL").getOrCreate()

# Read CSV properly
df = spark.read \
    .option("header", True) \
    .option("multiLine", True) \
    .option("quote", '"') \
    .option("escape", '"') \
    .csv("input_3_c.csv")

# Clean ONLY inner quotes, keep outer quotes intact
df_clean = df.withColumn(
    "SqlTextInfo",
    when(
        col("SqlTextInfo").isNotNull(),

        # Step 1: fix escaped quotes "" -> "
        regexp_replace(
            regexp_replace(
                col("SqlTextInfo"),
                r'""',
                '"'
            ),
            r'"',
            ''   # remove all inner quotes
        )
    ).otherwise(col("SqlTextInfo"))
)

df_clean.show(truncate=False)
