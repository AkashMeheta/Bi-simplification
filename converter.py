import re
import csv

def clean_sql_file(input_path, output_path):
    cleaned_queries = []

    with open(input_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            chunk = row.get('SqlTextInfo', '')

            # Normalize Windows line endings
            chunk = chunk.replace('\r\n', '\n').replace('\r', '\n')

            # Strip surrounding double quotes and whitespace
            chunk = re.sub(r'^[\s"]+|[\s"]+$', '', chunk)

            # Remove trailing semicolon if present (we'll add it back later)
            chunk = chunk.rstrip(';').strip()

            # Skip anything that doesn't start with SELECT
            if not re.match(r'SELECT\b', chunk, re.IGNORECASE):
                continue

            # Process LINE BY LINE to preserve -- comments
            lines = chunk.split('\n')
            cleaned_lines = []
            for line in lines:
                # Strip whitespace and stray quotes from each line
                line = line.strip()
                line = re.sub(r'^[\s"]+|[\s"]+$', '', line)
                if line:
                    # Replace internal double quotes with single quotes
                    line = line.replace('"', "'")
                    cleaned_lines.append(line)

            # Re-join with newlines so -- comments only affect their own line
            query = '\n'.join(cleaned_lines)

            # Wrap in double quotes with semicolon inside
            cleaned_queries.append(f'"{query};"')

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(cleaned_queries))

    print(f"Done. {len(cleaned_queries)} queries written to {output_path}")


# --- Run it ---
input_file  = 'text.csv'
output_file = 'cleaned_queries.txt'

clean_sql_file(input_file, output_file)
