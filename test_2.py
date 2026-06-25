from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("CustomParser").getOrCreate()

# Step 1: Read raw file line by line
df_raw = spark.read.text("input_3_c.csv")

lines = df_raw.rdd.map(lambda x: x[0]).collect()

rows = []
buffer = ""

for line in lines:
    if buffer:
        buffer += "\n" + line
    else:
        buffer = line

    # 🔥 End of one record
    if buffer.strip().endswith(';"'):
        rows.append(buffer)
        buffer = ""

# Step 2: Split into columns
parsed_data = []

for row in rows:
    try:
        first_comma = row.find(',')
        second_comma = row.find(',', first_comma + 1)

        date = row[:first_comma]
        user = row[first_comma + 1:second_comma]
        sql = row[second_comma + 1:].strip()

        # 🔥 Remove inner quotes only
        sql = sql.replace('""', '"').replace('"', '')

        parsed_data.append((date, user, sql))

    except:
        continue

# Step 3: Create DataFrame
df = spark.createDataFrame(parsed_data, ["Metric_Date", "Username", "SqlTextInfo"])

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
