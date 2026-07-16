from pyspark.sql.functions import col

# Alias for clarity
cm = column_metrics.alias("cm")
md = column_metadata.alias("md")

# Step 1: Tables that exist in metadata
md_tables = md.select("table_name").distinct()

# Step 2: Valid table-column combinations
valid_pairs = md.select("table_name", "column_name").distinct()

# Step 3: Rows where table exists in metadata
cm_with_md_table = cm.join(md_tables, on="table_name", how="inner")

# Step 4: Rows where table does NOT exist in metadata
cm_without_md_table = cm.join(md_tables, on="table_name", how="left_anti")

# Step 5: From existing tables → keep only valid column matches
cm_valid = cm_with_md_table.join(
    valid_pairs,
    on=["table_name", "column_name"],
    how="inner"
)

# Step 6: Final result
final_df = cm_valid.unionByName(cm_without_md_table)
