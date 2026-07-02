import time

# ---- CONFIG ----
input_path = "/Volumes/your_catalog/your_schema/your_volume/queries.csv"
output_path = "/Volumes/your_catalog/your_schema/your_volume/queries_cleaned.csv"

NUM_LEADING_FIELDS = 3   # username, database name, tablename
NUM_TRAILING_FIELDS = 2  # LogDate, starttime  <-- change to 3 if there really is a 4th trailing col

start = time.time()

with open(input_path, encoding="utf-8") as f:
    raw = f.read()

lines = raw.split("\n")
header = lines[0]
body = lines[1:]

# ---- Step 1: Reassemble multiline rows ----
# A new logical row starts when a line begins with 3 comma-separated
# non-quoted fields followed by a quote (start of sqlQueryTxt).
def looks_like_row_start(line):
    parts = line.split(",", NUM_LEADING_FIELDS)
    if len(parts) <= NUM_LEADING_FIELDS:
        return False
    return parts[NUM_LEADING_FIELDS].startswith('"')

rows = []
buffer = []
for line in body:
    if looks_like_row_start(line) and buffer:
        rows.append("\n".join(buffer))
        buffer = [line]
    else:
        buffer.append(line)
if buffer:
    rows.append("\n".join(buffer))

print(f"Reassembled {len(rows)} logical rows in {time.time()-start:.2f}s")

# ---- Step 2: Split each row using front/back field counting ----
cleaned_rows = []
skipped = []

t2 = time.time()
for row in rows:
    # Split off leading fields
    front_split = row.split(",", NUM_LEADING_FIELDS)
    if len(front_split) <= NUM_LEADING_FIELDS:
        skipped.append(row)
        continue
    leading = front_split[:NUM_LEADING_FIELDS]
    remainder = front_split[NUM_LEADING_FIELDS]  # starts with the query field's opening quote

    # Split off trailing fields from the back
    back_split = remainder.rsplit(",", NUM_TRAILING_FIELDS)
    if len(back_split) <= NUM_TRAILING_FIELDS:
        skipped.append(row)
        continue
    query_field = back_split[0]
    trailing = back_split[1:]

    # Strip outer quotes (if present) from query field
    q = query_field.strip()
    if q.startswith('"') and q.endswith('"') and len(q) >= 2:
        q_inner = q[1:-1]
    else:
        q_inner = q  # no outer quotes found, use as-is

    # Remove ALL internal double quotes from the query text
    cleaned_query = q_inner.replace('"', '')

    # Clean trailing fields too (strip stray outer quotes only, don't touch content)
    cleaned_trailing = []
    for t in trailing:
        t = t.strip()
        if t.startswith('"') and t.endswith('"') and len(t) >= 2:
            t = t[1:-1]
        cleaned_trailing.append(t)

    new_row = ",".join(leading) + ',"' + cleaned_query + '",' + ",".join(cleaned_trailing)
    cleaned_rows.append(new_row)

print(f"Cleaned {len(cleaned_rows)} rows, skipped {len(skipped)} in {time.time()-t2:.2f}s")

# ---- Step 3: Write output ----
with open(output_path, "w", encoding="utf-8") as f:
    f.write(header + "\n")
    f.write("\n".join(cleaned_rows))

if skipped:
    with open(output_path.replace(".csv", "_skipped.txt"), "w", encoding="utf-8") as f:
        f.write("\n---ROW---\n".join(skipped))

print(f"Total time: {time.time()-start:.2f}s")
