from pyspark.sql.window import Window

from pyspark.sql.functions import row_number, col



window_spec = Window.orderBy("row_wid")  # or any column to define order



df = df.withColumn(

    "row_wid",

    row_number().over(window_spec)

)





from pyspark.sql.functions import when, concat, lit



df = df.withColumn(

    "recommendation_id",

    when(

        col("source") == "databricks",

        concat(lit("DB"), col("recommendation_id"))

    ).otherwise(col("recommendation_id"))

)





