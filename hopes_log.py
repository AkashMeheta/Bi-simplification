from pyspark.sql.functions import col, coalesce

# Step 1: Join
df2_updated = (
    ddf.alias("t2")
    .join(
        mdf.alias("t1"),
        (col("t2.data_object_database_name") == col("t1.DatabaseName")) &
        (col("t2.Data_Product_Object_Name") == col("t1.TableName")),
        "left"
    )
    .withColumn(
        "column_count_updated",
        coalesce(col("t1.column_count"), col("t2.column_count"))
    )
)

# Step 2: Insert at index 7
insert_index = 7

# IMPORTANT: use original df columns
cols = [c for c in ddf.columns if c != "column_count"]

res_df = df2_updated.select(
    *cols[:insert_index],
    col("column_count_updated").alias("column_count"),
    *cols[insert_index:]
)

# Step 3: Display correct DF
res_df.display()




from delta.tables import DeltaTable

# Enable schema evolution (important for new columns)
spark.conf.set("spark.databricks.delta.schema.autoMerge.enabled", "true")

target = DeltaTable.forName(
    spark,
    "usm_dev.ccw_audmstr_rawz.data_product_column_metadata"
)

target.alias("t").merge(
    res_df.alias("s"),
    "t.TableName = s.TableName AND t.ColumnName = s.ColumnName"
).whenMatchedUpdate(set={
    "is_used_teradata": "s.is_used_teradata",
    "is_used_databricks": "s.is_used_databricks"
}).whenNotMatchedInsertAll().execute()
