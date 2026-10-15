-- Run against the project database. No GO batch separators are used.
IF SCHEMA_ID(N'bronze') IS NULL EXEC(N'CREATE SCHEMA [bronze]');
IF SCHEMA_ID(N'silver') IS NULL EXEC(N'CREATE SCHEMA [silver]');
IF SCHEMA_ID(N'gold') IS NULL EXEC(N'CREATE SCHEMA [gold]');

IF OBJECT_ID(N'bronze._file_loads', N'U') IS NULL
BEGIN
  CREATE TABLE [bronze].[_file_loads](
    [load_id] uniqueidentifier NOT NULL,
    [source_path] nvarchar(1024) NOT NULL,
    [sha256] char(64) NOT NULL,
    [table_name] sysname NOT NULL,
    [status] varchar(20) NOT NULL,
    [source_row_count] bigint NULL,
    [inserted_row_count] bigint NULL,
    [validated_row_count] bigint NULL,
    [started_at_utc] datetime2(3) NOT NULL,
    [completed_at_utc] datetime2(3) NULL,
    [error_message] nvarchar(4000) NULL,
    CONSTRAINT [PK_bronze_file_loads] PRIMARY KEY ([load_id])
  );
END;
