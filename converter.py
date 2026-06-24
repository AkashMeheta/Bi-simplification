import re
import csv

# Keywords that identify valid SQL queries
SQL_KEYWORDS = ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'WITH', 'CREATE', 'DROP', 'ALTER', 'MERGE', 'EXEC', 'CALL')

def clean_sql_file(input_path, output_path):
    cleaned_queries = []
    skipped = 0

    with open(input_path, 'r', encoding='utf-8-sig') as f:  # utf-8-sig handles BOM
        reader = csv.DictReader(f)

        # Strip hidden whitespace from column names
        reader.fieldnames = [name.strip() for name in reader.fieldnames]

        for row in reader:
            chunk = row.get('SqlTextInfo', '').strip()

            if not chunk:
                skipped += 1
                continue

            # Normalize Windows line endings
            chunk = chunk.replace('\r\n', '\n').replace('\r', '\n')

            # Strip surrounding double quotes and whitespace
            chunk = re.sub(r'^[\s"]+|[\s"]+$', '', chunk)

            # Remove trailing semicolon (we'll add it back later)
            chunk = chunk.rstrip(';').strip()

            # Skip if not a recognizable SQL statement
            if not re.match(rf'^\s*({"|".join(SQL_KEYWORDS)})\b', chunk, re.IGNORECASE):
                skipped += 1
                continue

            # Process LINE BY LINE to preserve -- comments
            lines = chunk.split('\n')
            cleaned_lines = []
            for line in lines:
                line = line.strip()
                line = re.sub(r'^[\s"]+|[\s"]+$', '', line)
                if line:
                    line = line.replace('"', "'")
                    cleaned_lines.append(line)

            # Re-join with newlines so -- comments only affect their own line
            query = '\n'.join(cleaned_lines)

            # Wrap in double quotes with semicolon inside
            cleaned_queries.append(f'"{query};"')

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(cleaned_queries))

    print(f"Done. {len(cleaned_queries)} queries written to {output_path}")
    print(f"Skipped {skipped} rows (empty or unrecognized SQL)")


# --- Run it ---
input_file  = 'text.csv'
output_file = 'cleaned_queries.txt'

clean_sql_file(input_file, output_file)
