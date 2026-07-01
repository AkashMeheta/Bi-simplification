# =============================================================================
# llm_table_pipeline.py
# Databricks PySpark Script
# Flow: Fetch 4 Delta tables → Build prompt → Call LiteLLM API → Save CSV
# =============================================================================


from pyspark.sql import SparkSession
from pyspark.sql.window import Window
from pyspark.sql.functions import row_number, col

# Create Spark session
spark = SparkSession.builder.appName("RowWidExample").getOrCreate()

# Read CSV
df = spark.read.csv("/path/to/file.csv", header=True, inferSchema=True)

# Define window (order by any column or multiple columns)
window_spec = Window.orderBy(col("your_column_name"))

# Add incremental row_wid
df_with_id = df.withColumn("row_wid", row_number().over(window_spec))

# Save as table
df_with_id.write.mode("overwrite").saveAsTable("your_database.your_table")







import json
import requests
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StringType
import io
import csv

# =============================================================================
# CONFIG — update these before running
# =============================================================================

# LiteLLM Gateway — pick ONE endpoint from your screenshot
LITELLM_API_URL = "https://litellm.ai-coe-test.aws.evernorthcloud.com/v1/chat/completions"
MODEL_NAME      = "claude-sonnet-4-6"

# API key stored in Databricks secrets (scope/key to set up in your workspace)
# dbutils.secrets.put(scope="llm-scope", key="litellm-api-key", string_value="<your-key>")
API_KEY = dbutils.secrets.get(scope="llm-scope", key="litellm-api-key")

# Unity Catalog table paths (catalog.schema.table)
TABLE_1 = "dev_catalog.sales.orders"
TABLE_2 = "dev_catalog.sales.customers"
TABLE_3 = "dev_catalog.inventory.products"
TABLE_4 = "dev_catalog.finance.transactions"

# Output path for the CSV result (DBFS or Unity Catalog Volume)
OUTPUT_PATH = "/dbfs/tmp/llm_output/analysis_result.csv"

# Max rows per table to include in the prompt (keep token cost low)
MAX_ROWS_PER_TABLE = 20

# =============================================================================
# DUMMY DATA — replaces real tables for local/dev testing
# Set USE_DUMMY_DATA = False to pull real Databricks tables
# =============================================================================

USE_DUMMY_DATA = True  # ← flip to False for production

DUMMY_TABLES = {
    "orders": [
        {"order_id": "ORD001", "customer_id": "C01", "product_id": "P01", "qty": 3,  "amount": 150.0, "status": "completed"},
        {"order_id": "ORD002", "customer_id": "C02", "product_id": "P02", "qty": 1,  "amount": 89.99, "status": "pending"},
        {"order_id": "ORD003", "customer_id": "C01", "product_id": "P03", "qty": 5,  "amount": 275.0, "status": "completed"},
        {"order_id": "ORD004", "customer_id": "C03", "product_id": "P01", "qty": 2,  "amount": 100.0, "status": "cancelled"},
    ],
    "customers": [
        {"customer_id": "C01", "name": "Alice Johnson", "region": "North", "segment": "Premium"},
        {"customer_id": "C02", "name": "Bob Smith",     "region": "South", "segment": "Standard"},
        {"customer_id": "C03", "name": "Carol White",   "region": "East",  "segment": "Premium"},
    ],
    "products": [
        {"product_id": "P01", "name": "Widget A", "category": "Hardware", "unit_price": 50.0,  "stock": 200},
        {"product_id": "P02", "name": "Gadget B", "category": "Software", "unit_price": 89.99, "stock": 50},
        {"product_id": "P03", "name": "Tool C",   "category": "Hardware", "unit_price": 55.0,  "stock": 120},
    ],
    "transactions": [
        {"txn_id": "T001", "order_id": "ORD001", "payment_method": "Credit Card", "txn_date": "2025-01-10", "amount": 150.0},
        {"txn_id": "T002", "order_id": "ORD002", "payment_method": "PayPal",      "txn_date": "2025-01-11", "amount": 89.99},
        {"txn_id": "T003", "order_id": "ORD003", "payment_method": "Credit Card", "txn_date": "2025-01-12", "amount": 275.0},
    ],
}

# =============================================================================
# PROMPT TEMPLATE
# The LLM is told to return CSV with a fixed schema
# =============================================================================

EXPECTED_CSV_SCHEMA = "customer_id,customer_name,region,segment,total_orders,total_revenue,top_product,risk_flag"

PROMPT_TEMPLATE = """
You are a data analyst. Below are four business tables extracted from our data warehouse.
Analyze the data and return a summary report.

### Tables:

{table_data}

### Task:
For each customer, calculate:
- total number of completed orders
- total revenue from completed orders
- their top purchased product (by quantity)
- a risk_flag: "HIGH" if any order was cancelled, else "LOW"

### Output Format:
Return ONLY a valid CSV with NO explanation, NO markdown, NO backticks.
The CSV must have EXACTLY this header row followed by data rows:

{csv_schema}

Rules:
- First line must be the header exactly as shown above
- Use comma as delimiter
- Wrap any field containing commas in double quotes
- Do not include any text before or after the CSV
""".strip()

# =============================================================================
# STEP 1 — Fetch tables (dummy or real Databricks)
# =============================================================================

def fetch_tables_as_dicts():
    """Returns dict of {table_name: list_of_row_dicts}"""
    if USE_DUMMY_DATA:
        print("[INFO] Using dummy data (USE_DUMMY_DATA=True)")
        return DUMMY_TABLES

    print("[INFO] Fetching real tables from Databricks Unity Catalog...")
    spark = SparkSession.builder.getOrCreate()
    tables = {}

    table_map = {
        "orders":       TABLE_1,
        "customers":    TABLE_2,
        "products":     TABLE_3,
        "transactions": TABLE_4,
    }

    for alias, full_table_name in table_map.items():
        try:
            df = spark.table(full_table_name).limit(MAX_ROWS_PER_TABLE)
            rows = [row.asDict() for row in df.collect()]
            tables[alias] = rows
            print(f"  ✓ {full_table_name} → {len(rows)} rows")
        except Exception as e:
            print(f"  ✗ Failed to load {full_table_name}: {e}")
            tables[alias] = []

    return tables

# =============================================================================
# STEP 2 — Format tables into prompt text
# =============================================================================

def format_tables_for_prompt(tables: dict) -> str:
    """Converts each table dict list into a readable markdown-style block."""
    sections = []
    for table_name, rows in tables.items():
        if not rows:
            sections.append(f"**{table_name.upper()}**\n(no data)")
            continue
        headers = list(rows[0].keys())
        header_line = " | ".join(headers)
        separator   = " | ".join(["---"] * len(headers))
        data_lines  = [" | ".join(str(row.get(h, "")) for h in headers) for row in rows]
        table_block = f"**{table_name.upper()}**\n{header_line}\n{separator}\n" + "\n".join(data_lines)
        sections.append(table_block)
    return "\n\n".join(sections)

# =============================================================================
# STEP 3 — Call LiteLLM API
# =============================================================================

def call_litellm_api(prompt: str) -> str:
    """Posts the prompt to the LiteLLM gateway and returns the response text."""
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }
    payload = {
        "model": MODEL_NAME,
        "max_tokens": 2048,
        "temperature": 0,           # deterministic output for structured CSV
        "messages": [
            {
                "role": "system",
                "content": "You are a precise data analyst. Always return only valid CSV output, no extra text."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    }

    print(f"[INFO] Calling LiteLLM API: {LITELLM_API_URL}")
    response = requests.post(
        LITELLM_API_URL,
        headers=headers,
        json=payload,
        timeout=60
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"API call failed [{response.status_code}]: {response.text}"
        )

    data = response.json()
    raw_text = data["choices"][0]["message"]["content"].strip()
    print(f"[INFO] API response received ({len(raw_text)} chars)")
    return raw_text

# =============================================================================
# STEP 4 — Validate & clean the CSV response
# =============================================================================

def clean_csv_response(raw_text: str, expected_header: str) -> str:
    """
    Strips markdown fences if present, validates the header row,
    and returns clean CSV text.
    """
    # Strip markdown code fences if LLM wrapped output
    lines = raw_text.strip().splitlines()
    cleaned = [
        line for line in lines
        if not line.strip().startswith("```")
    ]
    csv_text = "\n".join(cleaned).strip()

    # Validate header
    first_line = csv_text.splitlines()[0].strip()
    if first_line != expected_header:
        print(f"[WARN] Header mismatch!")
        print(f"  Expected : {expected_header}")
        print(f"  Got      : {first_line}")
    else:
        print("[INFO] CSV header validated ✓")

    return csv_text

# =============================================================================
# STEP 5 — Save CSV to DBFS / Volume
# =============================================================================

def save_csv(csv_text: str, output_path: str):
    """Writes CSV text to DBFS or local path."""
    import os
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(csv_text)

    print(f"[INFO] CSV saved → {output_path}")

    # Optionally register as a Delta table in Unity Catalog
    # (uncomment if you want to persist as a queryable table)
    # spark = SparkSession.builder.getOrCreate()
    # df = spark.read.option("header", True).csv(output_path)
    # df.write.mode("overwrite").saveAsTable("dev_catalog.analysis.llm_customer_summary")

# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 60)
    print("  LLM Table Analysis Pipeline")
    print("=" * 60)

    # Step 1: Fetch tables
    tables = fetch_tables_as_dicts()

    # Step 2: Build prompt
    table_data_str = format_tables_for_prompt(tables)
    final_prompt   = PROMPT_TEMPLATE.format(
        table_data=table_data_str,
        csv_schema=EXPECTED_CSV_SCHEMA
    )
    print(f"\n[INFO] Prompt built ({len(final_prompt)} chars)")

    # Step 3: Call LLM
    raw_response = call_litellm_api(final_prompt)

    # Step 4: Clean & validate CSV
    clean_csv = clean_csv_response(raw_response, EXPECTED_CSV_SCHEMA)

    # Step 5: Save output
    save_csv(clean_csv, OUTPUT_PATH)

    # Print preview
    print("\n--- CSV Output Preview ---")
    for line in clean_csv.splitlines()[:10]:
        print(line)
    print("-" * 26)
    print("\n[DONE] Pipeline completed successfully.")

# Entry point
main()
