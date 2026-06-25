from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("CustomParser").getOrCreate()

# ── Step 1: Read raw file ────────────────────────────────────────────────────
df_raw = spark.read.text("input_3_c.csv")
lines = df_raw.rdd.map(lambda x: x[0]).collect()

rows = []
buffer = ""

for line in lines:
    buffer = (buffer + "\n" + line) if buffer else line

    stripped = buffer.strip()

    # FIX 1: A complete record ends with a closing double-quote that terminates
    # the SQL field.  The original check `endswith(';"')` was too narrow — it
    # required a literal semicolon right before the closing quote, which only
    # matches some SQL statements.  We instead look for any line whose last
    # non-whitespace character is a double-quote AND that contains at least
    # two commas (sql, user, date), which is a reliable "end of record" signal.
    #
    # Adjust this condition to match whatever your actual record terminator is.
    # Common alternatives:
    #   stripped.endswith('"')          ← SQL field is always last, quoted
    #   stripped.endswith(';"')         ← SQL always ends with semicolon+quote
    #   '"' in stripped and stripped.count(',') >= 2  ← lenient version
    if stripped.endswith('"') and stripped.count(',') >= 2:
        rows.append(stripped)
        buffer = ""

# FIX 2: Don't silently drop the last record if the file has no trailing newline
if buffer.strip():
    rows.append(buffer.strip())

print(f"[INFO] Total records collected: {len(rows)}")

# ── Step 2: Parse each record ────────────────────────────────────────────────
parsed_data = []
skipped = []

for i, row in enumerate(rows):
    try:
        # The format is:  "<SQL_TEXT>",<user>,<date>
        # SQL may contain commas, so we cannot simply split(",").
        # Strategy: find the LAST two commas — date is after the last comma,
        # user is between the two last commas, everything before is SQL.

        last_comma = row.rfind(',')
        if last_comma == -1:
            raise ValueError("No comma found — not a valid row")

        second_last_comma = row.rfind(',', 0, last_comma)
        if second_last_comma == -1:
            raise ValueError("Only one comma found — cannot split user/date")

        sql  = row[:second_last_comma].strip()
        user = row[second_last_comma + 1 : last_comma].strip()
        date = row[last_comma + 1:].strip()

        # FIX 3: SQL cleaning — unescape doubled quotes ("" → ") but do NOT
        # blindly remove every remaining quote afterwards.  The original code
        # did `.replace('""', '"').replace('"', '')` which first unescaped
        # then deleted every quote, corrupting string literals inside the SQL.
        if sql.startswith('"') and sql.endswith('"'):
            inner_sql = sql[1:-1]           # strip the outer CSV quotes
            inner_sql = inner_sql.replace('""', '"')   # unescape only
            sql = inner_sql                  # store without the outer CSV quotes
            # If you need to preserve outer quotes in the DataFrame, use:
            # sql = f'"{inner_sql}"'

        # FIX 4: Basic validation before appending
        if not sql or not user or not date:
            raise ValueError(f"Empty field(s): sql={bool(sql)}, user={bool(user)}, date={bool(date)}")

        parsed_data.append((sql, user, date))

    except Exception as e:
        skipped.append((i, str(e), row[:120]))   # log instead of silent skip
        continue

print(f"[INFO] Parsed: {len(parsed_data)}  |  Skipped: {len(skipped)}")
if skipped:
    print("[WARN] Skipped rows (index, reason, preview):")
    for idx, reason, preview in skipped:
        print(f"  Row {idx}: {reason} | {preview!r}")

# ── Step 3: Guard against empty result before creating DataFrame ─────────────
# FIX 5: createDataFrame([]) with a schema works, but show() on it is
# misleading (prints nothing).  Warn the user explicitly.
if not parsed_data:
    print("[ERROR] No rows were parsed.  Check your record-end condition in Step 1.")
    spark.stop()
    raise SystemExit("Aborting — empty dataset.")

df = spark.createDataFrame(parsed_data, ["SqlTextInfo", "users", "Metric_Date"])
df.show(truncate=False)
