WITH query_lengths AS (

    SELECT 

        LogDate,

        SqlTextInfo,

        LENGTH(SqlTextInfo) AS query_length

    FROM your_table

),

ranked_queries AS (

    SELECT *,

           ROW_NUMBER() OVER (PARTITION BY LogDate ORDER BY query_length DESC) AS rn

    FROM query_lengths

)

SELECT 

    LogDate,

    SqlTextInfo AS longest_query,

    query_length

FROM ranked_queries

WHERE rn = 1

ORDER BY LogDate DESC;













WITH query_counts AS (

    SELECT 

        LogDate,

        SqlTextInfo,

        COUNT(*) AS usage_count

    FROM your_table

    GROUP BY LogDate, SqlTextInfo

),

ranked_queries AS (

    SELECT *,

           ROW_NUMBER() OVER (PARTITION BY LogDate ORDER BY usage_count DESC) AS rn

    FROM query_counts

)

SELECT 

    LogDate,

    SqlTextInfo AS most_used_query,

    usage_count

FROM ranked_queries

WHERE rn = 1

ORDER BY LogDate DESC;
