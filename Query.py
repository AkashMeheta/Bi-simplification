import re
import time

# ---- CONFIG ----
input_path = "/Volumes/your_catalog/your_schema/your_volume/queries.csv"
output_path = "/Volumes/your_catalog/your_schema/your_volume/queries_cleaned.csv"
TRAILING_FIELDS = 2  # LogDate, starttime — adjust if your real file has more

start = time.time()

with open(input_path, encoding="utf-8") as f:
    raw = f.read()

lines = raw.split("\n")
header = lines[0]
body = lines[1:]

# Step 1: Reassemble logical rows (a row may span multiple physical lines
# because sqlQueryTxt contains embedded newlines).
# A new row starts when a line begins with: field1,field2,field3,"
START_PATTERN = re.compile(r'^[^,]+,[^,]+,[^,]+,"')

rows = []
buffer = []
for line in body:
    if START_PATTERN.match(line) and buffer:
        rows.append("\n".join(buffer))
        buffer = [line]
    else:
        buffer.append(line)
if buffer:
    rows.append("\n".join(buffer))

print(f"Reassembled {len(rows)} logical rows in {time.time()-start:.2f}s")

# Step 2: For each row, split off the first 3 fields, then find the
# sqlQueryTxt field by locating the outer quotes that wrap it, using the
# known number of trailing fields as an anchor from the end of the row.
row_regex = re.compile(
    r'^(?P<prefix>[^,]+,[^,]+,[^,]+),"(?P<query>.*)"' +
    r'(?P<trailing>(?:,"[^"]*"){' + str(TRAILING_FIELDS) + r'})$',
    re.DOTALL
)

cleaned_rows = []
skipped = []

t2 = time.time()
for row in rows:
    m = row_regex.match(row)
    if not m:
        skipped.append(row)
        continue
    prefix = m.group("prefix")
    query = m.group("query")
    trailing = m.group("trailing")

    # Strip ALL internal double quotes from the query content only
    cleaned_query = query.replace('"', '')

    cleaned_rows.append(f'{prefix},"{cleaned_query}"{trailing}')

print(f"Cleaned {len(cleaned_rows)} rows, skipped {len(skipped)} in {time.time()-t2:.2f}s")

# Step 3: Write output
with open(output_path, "w", encoding="utf-8") as f:
    f.write(header + "\n")
    f.write("\n".join(cleaned_rows))

if skipped:
    with open(output_path.replace(".csv", "_skipped.txt"), "w", encoding="utf-8") as f:
        f.write("\n---ROW---\n".join(skipped))

print(f"Total time: {time.time()-start:.2f}s")
