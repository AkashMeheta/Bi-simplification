from pyspark.sql import functions as F

raw_df = spark.read.csv(
    "/path/to/your.csv",
    header=True,
    inferSchema=False,
    multiLine=True,
    escape='"',
    quote='"'
)

def resolve_col(df, expected_name):
    matches = [c for c in df.columns if c.lower() == expected_name.lower()]
    if not matches:
        raise ValueError(f"Column '{expected_name}' not found. Available columns: {df.columns}")
    return matches[0]

col_starttime    = resolve_col(raw_df, "starttime")
col_lastresponse = resolve_col(raw_df, "LastResponseTime")

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

FAKE_NULL_TOKENS = {"null", "none", "na", "n/a", "nat", "-", "nil"}

def clean_raw_string(colname):
    trimmed = F.trim(F.col(colname))
    is_fake_null = F.lower(trimmed).isin(list(FAKE_NULL_TOKENS)) | (trimmed == "")
    return F.when(is_fake_null, None).otherwise(trimmed)

def build_timestamp_expr(colname):
    cleaned = clean_raw_string(colname)
    attempts = [F.try_to_timestamp(cleaned, F.lit(fmt)) for fmt in TIMESTAMP_FORMATS]
    return F.coalesce(*attempts)

starttime_ts_expr    = build_timestamp_expr(col_starttime)
lastresponse_ts_expr = build_timestamp_expr(col_lastresponse)

# --- Keep every original column, but swap in the normalized timestamp
#     expressions for the two we're fixing, and append audit flags ---
other_cols = [c for c in raw_df.columns if c not in (col_starttime, col_lastresponse)]

select_exprs = (
    [F.col(c) for c in other_cols]
    + [
        starttime_ts_expr.alias(col_starttime),
        lastresponse_ts_expr.alias(col_lastresponse),
        (clean_raw_string(col_starttime).isNotNull() & starttime_ts_expr.isNull())
            .alias("starttime_parse_failed"),
        (clean_raw_string(col_lastresponse).isNotNull() & lastresponse_ts_expr.isNull())
            .alias("lastresponsetime_parse_failed"),
    ]
)

final_df = raw_df.select(*select_exprs)

final_df.printSchema()
final_df.show(truncate=False)
final_df.write.mode("overwrite").format("delta").saveAsTable("your_catalog.your_schema.your_table")
