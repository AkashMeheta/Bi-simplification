import re
import csv
import io

# Adjust this to match wherever your file actually lives
input_path = "/Volumes/your_catalog/your_schema/your_volume/queries.csv"
skipped_log_path = "/Volumes/your_catalog/your_schema/your_volume/skipped_queries.txt"

def repair_csv_lines(filepath):
    with open(filepath, encoding="utf-8") as f:
        raw = f.read()

    lines = raw.split("\n")
    header = lines[0]
    body_lines = lines[1:]

    repaired_rows = []
    buffer = ""
    START_PATTERN = re.compile(r"^[\w\-\.]+,[\w\-\.]+,[\w\-\.]+,\"")

    for line in body_lines:
        if START_PATTERN.match(line) and buffer:
            repaired_rows.append(buffer)
            buffer = line
        else:
            buffer = buffer + "\n" + line if buffer else line
    if buffer:
        repaired_rows.append(buffer)

    return header, repaired_rows


def fix_and_split_row(row_text):
    parts = row_text.split(",", 3)
    if len(parts) < 4:
        return None

    prefix = parts[:3]
    rest = parts[3]

    tail_match = re.search(r'",(\d{4}-\d{2}-\d{2}),(\d{2}:\d{2}:\d{2})\s*$', rest)
    if not tail_match:
        return None

    query_field = rest[1:tail_match.start()]
    log_date, start_time = tail_match.group(1), tail_match.group(2)
    fixed_query = query_field.replace('"', '""')

    return prefix + [fixed_query, log_date, start_time]


header, raw_rows = repair_csv_lines(input_path)
good_rows = []
skipped_rows = []

for row_text in raw_rows:
    result = fix_and_split_row(row_text)
    if result is None:
        skipped_rows.append(row_text)
    else:
        good_rows.append(result)

print(f"Parsed OK: {len(good_rows)}, Skipped: {len(skipped_rows)}")

# Write skipped rows to log
if skipped_rows:
    with open(skipped_log_path, "w", encoding="utf-8") as f:
        f.write("\n---ROW---\n".join(skipped_rows))
def repair_csv_lines(filepath):

    with open(filepath, encoding="utf-8") as f:

        raw = f.read()



    lines = raw.split("\n")

    header = lines[0]

    body_lines = lines[1:]



    repaired_rows = []

    buffer_parts = []

    START_PATTERN = re.compile(r"^[\w\-\.]+,[\w\-\.]+,[\w\-\.]+,\"")



    for line in body_lines:

        if START_PATTERN.match(line) and buffer_parts:

            repaired_rows.append("\n".join(buffer_parts))

            buffer_parts = [line]

        else:

            buffer_parts.append(line)

    if buffer_parts:

        repaired_rows.append("\n".join(buffer_parts))



    return header, repaired_rows.  for i, row_text in enumerate(raw_rows):

    if i % 1000 == 0:

        print(f"Processed {i}/{len(raw_rows)} rows...")

    result = fix_and_split_row(row_text)

    ...test_line = 'alice,mydb,mytable,"SELECT...'

print(bool(START_PATTERN.match(test_line)))
