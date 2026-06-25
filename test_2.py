import pandas as pd
import re

INPUT_FILE  = "input_3_c.csv"
OUTPUT_FILE = "output_parsed.xlsx"

# ── Step 1: Read the entire file as raw text ─────────────────────────────────
with open(INPUT_FILE, "r", encoding="utf-8") as f:
    content = f.read()

# ── Step 2: Skip the header line ─────────────────────────────────────────────
lines = content.splitlines()
# First line is:  SqlTextInfo,Metric_Date,users  → skip it
body = "\n".join(lines[1:])

# ── Step 3: Split into individual records ────────────────────────────────────
# From the CSV every record looks like:
#
#   "SELECT ...
#    ...
#    AND something = 'ESI';","2026-03-30",C8V4SJ
#
# i.e.  opening "  ...multiline SQL...  ;" , "date" , user
#
# Regex breakdown:
#   "           →  literal opening double-quote of the SQL field
#   (.*?)       →  SQL content (non-greedy, DOTALL so . matches newlines)
#   ;"          →  SQL always ends with semicolon then closing double-quote
#   \s*,\s*     →  comma separator (allow spaces)
#   "([^"]+)"   →  date field in double quotes
#   \s*,\s*     →  comma separator
#   (\S+)       →  user id (no spaces)

RECORD_PATTERN = re.compile(
    r'"(.*?);"\s*,\s*"([^"]+)"\s*,\s*(\S+)',
    re.DOTALL
)

records = RECORD_PATTERN.findall(body)
print(f"[INFO] Total records found: {len(records)}")

# ── Step 4: Clean each SQL block and build rows ───────────────────────────────
parsed_rows = []
skipped     = []

for i, (raw_sql, date, user) in enumerate(records):
    try:
        # raw_sql is everything between the outer CSV quotes (semicolon included at end)
        # It may contain "" (CSV-escaped double quotes) → unescape to single "
        sql = raw_sql.replace('""', '"')

        # Strip leading/trailing whitespace
        sql = sql.strip()

        # Collapse runs of 3+ blank lines to keep SQL readable
        sql = re.sub(r'\n{3,}', '\n\n', sql)

        date = date.strip()
        user = user.strip()

        if not sql:
            raise ValueError("Empty SQL after cleaning")

        parsed_rows.append({
            "SqlTextInfo": sql,
            "Metric_Date": date,
            "users":       user,
        })

    except Exception as e:
        skipped.append((i, str(e), raw_sql[:80]))

print(f"[INFO] Parsed: {len(parsed_rows)}  |  Skipped: {len(skipped)}")
if skipped:
    print("[WARN] Skipped records:")
    for idx, reason, preview in skipped:
        print(f"  #{idx}: {reason} | {preview!r}")

# ── Step 5: Build DataFrame ───────────────────────────────────────────────────
if not parsed_rows:
    raise SystemExit("[ERROR] No records parsed. Check INPUT_FILE path and record format.")

df = pd.DataFrame(parsed_rows, columns=["SqlTextInfo", "Metric_Date", "users"])

print(f"\n[INFO] DataFrame shape: {df.shape}")
print(df[["Metric_Date", "users"]].to_string())
print("\n--- First SQL preview (first 300 chars) ---")
print(df["SqlTextInfo"].iloc[0][:300])

# ── Step 6: Save to Excel ─────────────────────────────────────────────────────
df.to_excel(OUTPUT_FILE, index=False)
print(f"\n[INFO] Saved to {OUTPUT_FILE}")
