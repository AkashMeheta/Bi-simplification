from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("CustomParser").getOrCreate()

# Step 1: Read raw file
df_raw = spark.read.text("input_3_c.csv")
lines = df_raw.rdd.map(lambda x: x[0]).collect()

rows = []
buffer = ""

for line in lines:
    if buffer:
        buffer += "\n" + line
    else:
        buffer = line

    # End of one record
    if buffer.strip().endswith(';"'):
        rows.append(buffer)
        buffer = ""

# Step 2: Parse based on NEW order
parsed_data = []

for row in rows:
    try:
        # Find LAST two commas (since SQL can contain commas)
        last_comma = row.rfind(',')
        second_last_comma = row.rfind(',', 0, last_comma)

        sql = row[:second_last_comma].strip()
        user = row[second_last_comma + 1:last_comma].strip()
        date = row[last_comma + 1:].strip()

        # 🔥 Clean SQL (keep outer quotes, remove inner quotes)
        if sql.startswith('"') and sql.endswith('"'):
            inner_sql = sql[1:-1]
            inner_sql = inner_sql.replace('""', '"').replace('"', '')
            sql = f'"{inner_sql}"'

        parsed_data.append((sql, user, date))

    except Exception as e:
        continue

# Step 3: Create DataFrame
df = spark.createDataFrame(parsed_data, ["SqlTextInfo", "users", "Metric_Date"])

df.show(truncate=False)
----------------------------------
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
