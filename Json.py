def extract_tables(llm_response):
    import re, json

    # Extract code block
    match = re.search(r"```(.*?)```", llm_response, re.DOTALL)
    if not match:
        raise ValueError("No code block found")

    block = match.group(1).strip()

    # Remove 'json'
    if block.lower().startswith("json"):
        block = block[4:].strip()

    # Extract tables
    t1 = re.search(r"Table1:\s*(\[[\s\S]*?\])", block)
    t2 = re.search(r"Table2:\s*(\[[\s\S]*?\])", block)

    if not t1 or not t2:
        raise ValueError("Tables not found")

    return json.loads(t1.group(1)), json.loads(t2.group(1))


table1_data, table2_data = extract_tables(llm_response)

df1 = pd.DataFrame(table1_data)
df2 = pd.DataFrame(table2_data)
