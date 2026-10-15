SELECT s.name AS schema_name, t.name AS table_name
FROM sys.schemas AS s
LEFT JOIN sys.tables AS t ON t.schema_id = s.schema_id
WHERE s.name IN (N'bronze', N'silver', N'gold')
ORDER BY s.name, t.name;

SELECT [load_id], [source_path], [sha256], [table_name], [status],
       [source_row_count], [inserted_row_count], [validated_row_count],
       [started_at_utc], [completed_at_utc], [error_message]
FROM [bronze].[_file_loads]
ORDER BY [started_at_utc] DESC;
