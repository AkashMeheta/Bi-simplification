
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

col_logdate      = resolve_col(raw_df, "LogDate")
col_starttime    = resolve_col(raw_df, "starttime")
col_lastresponse = resolve_col(raw_df, "LastResponseTime")

FAKE_NULL_TOKENS = {"null", "none", "na", "n/a", "nat", "-", "nil"}

def clean_raw_string(colname):
    trimmed = F.trim(F.col(colname))
    is_fake_null = F.lower(trimmed).isin(list(FAKE_NULL_TOKENS)) | (trimmed == "")
    return F.when(is_fake_null, None).otherwise(trimmed)

# --- Parse LogDate (format: M/d/yyyy, e.g. "4/9/2026") ---
def build_date_expr(colname):
    cleaned = clean_raw_string(colname)
    return F.coalesce(
        F.try_to_timestamp(cleaned, F.lit("M/d/yyyy")).cast("date"),
        F.try_to_timestamp(cleaned, F.lit("MM/dd/yyyy")).cast("date"),
        F.try_to_timestamp(cleaned, F.lit("yyyy-MM-dd")).cast("date"),
    )

logdate_expr = build_date_expr(col_logdate)

# --- Parse either "mm:ss.f" (2 parts) or "HH:mm:ss.f" (3 parts) into total seconds ---
PATTERN_2PART = r'^(\d{1,3}):(\d{1,2}(?:\.\d+)?)$'          # mm:ss.f
PATTERN_3PART = r'^(\d{1,3}):(\d{1,2}):(\d{1,2}(?:\.\d+)?)$'  # HH:mm:ss.f

def build_seconds_expr(colname):
    cleaned = clean_raw_string(colname)

    is_2part = cleaned.rlike(PATTERN_2PART)
    is_3part = cleaned.rlike(PATTERN_3PART)

    # 2-part: minutes:seconds
    m2 = F.regexp_extract(cleaned, PATTERN_2PART, 1).cast("double")
    s2 = F.regexp_extract(cleaned, PATTERN_2PART, 2).cast("double")
    secs_2part = (m2 * 60) + s2

    # 3-part: hours:minutes:seconds
    h3 = F.regexp_extract(cleaned, PATTERN_3PART, 1).cast("double")
    m3 = F.regexp_extract(cleaned, PATTERN_3PART, 2).cast("double")
    s3 = F.regexp_extract(cleaned, PATTERN_3PART, 3).cast("double")
    secs_3part = (h3 * 3600) + (m3 * 60) + s3

    return (
        F.when(is_3part, secs_3part)
         .when(is_2part, secs_2part)
         .otherwise(F.lit(None).cast("double"))
    )

starttime_secs_expr    = build_seconds_expr(col_starttime)
lastresponse_secs_expr = build_seconds_expr(col_lastresponse)

# --- Combine LogDate + seconds-of-day into a real timestamp ---
def build_timestamp_from_date_and_secs(date_expr, secs_expr):
    base_ts = date_expr.cast("timestamp").cast("long")  # midnight of LogDate, as epoch seconds
    return F.when(
        date_expr.isNotNull() & secs_expr.isNotNull(),
        (base_ts + secs_expr).cast("timestamp")
    ).otherwise(F.lit(None).cast("timestamp"))

starttime_ts_expr    = build_timestamp_from_date_and_secs(logdate_expr, starttime_secs_expr)
lastresponse_ts_expr = build_timestamp_from_date_and_secs(logdate_expr, lastresponse_secs_expr)

# --- Duration in seconds: LastResponseTime - StartTime (NULL if either side missing) ---
duration_seconds_expr = F.when(
    starttime_secs_expr.isNotNull() & lastresponse_secs_expr.isNotNull(),
    lastresponse_secs_expr - starttime_secs_expr
).otherwise(F.lit(None).cast("double"))

# --- Keep every original column, swap in normalized values, append audit flags ---
other_cols = [c for c in raw_df.columns if c not in (col_logdate, col_starttime, col_lastresponse)]

select_exprs = (
    [F.col(c) for c in other_cols]
    + [
        logdate_expr.alias(col_logdate),
        starttime_ts_expr.alias(col_starttime),
        lastresponse_ts_expr.alias(col_lastresponse),
        duration_seconds_expr.alias("Execution_Time"),
        (clean_raw_string(col_starttime).isNotNull() & starttime_secs_expr.isNull())
            .alias("starttime_parse_failed"),
        (clean_raw_string(col_lastresponse).isNotNull() & lastresponse_secs_expr.isNull())
            .alias("lastresponsetime_parse_failed"),
    ]
)

final_df = raw_df.select(*select_exprs)

final_df.printSchema()
final_df.show(truncate=False)

final_df.write.mode("overwrite").format("delta").saveAsTable("your_catalog.your_schema.your_table")





















______________


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
    "M/d/yyyy H:mm:ss",       # unpadded month/day/hour
    "dd/MM/yyyy HH:mm:ss",
    "d/M/yyyy H:mm:ss",       # unpadded day/month/hour
    "MM-dd-yyyy HH:mm:ss",
    "yyyy/MM/dd HH:mm:ss",
    # date-only fallbacks (no time component)
    "yyyy-MM-dd",
    "MM/dd/yyyy",
    "M/d/yyyy",               # <-- catches "4/9/2026"
    "dd/MM/yyyy",
    "d/M/yyyy",
    "MM-dd-yyyy",
    "yyyy/MM/dd",
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

duration_seconds_expr = F.when(
    starttime_ts_expr.isNotNull() & lastresponse_ts_expr.isNotNull() & (lastresponse_ts_expr > starttime_ts_expr),
    lastresponse_ts_expr.cast("long") - starttime_ts_expr.cast("long")
).otherwise(F.lit(None).cast("long"))

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
