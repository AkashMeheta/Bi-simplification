import re

def clean_sql_file(input_path, output_path):
    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Normalize Windows line endings
    content = content.replace('\r\n', '\n').replace('\r', '\n')

    # Split on semicolons — each piece is one query
    raw_chunks = content.split(';')

    cleaned_queries = []
    for chunk in raw_chunks:
        # Strip surrounding double quotes and whitespace from the whole chunk
        chunk = re.sub(r'^[\s"]+|[\s"]+$', '', chunk)

        # Skip anything that doesn't start with SELECT
        if not re.match(r'SELECT\b', chunk, re.IGNORECASE):
            continue

        # Process LINE BY LINE to preserve -- comments
        # (collapsing newlines would turn everything after -- into a comment)
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
input_file  = 'text.csv'             # change to your input file path
output_file = 'cleaned_queries.txt'  # change to your desired output path

clean_sql_file(input_file, output_file)
