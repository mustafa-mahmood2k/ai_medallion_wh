# Workflow and connection notes

1. `scripts/setup.sh` creates `.venv` and installs the two pinned dependency ranges.
2. `scripts/load.sh --preflight` reads local CSVs only and reports file size, hash, encoding, headers, row count, table mapping, and conflicts.
3. `scripts/load.sh --setup-only` connects to `master` with autocommit, checks `SELECT @@VERSION`, creates `ai_medallion_wh` only if absent, then creates the medallion schemas and ledger.
4. Review all preflight mappings before running the normal `scripts/load.sh` command. That command rechecks inputs and loads each file transactionally with count validation.

The default SQL Server endpoint is `localhost:1433`, user `sa`, database `ai_medallion_wh`. Use `Encrypt=no` and `TrustServerCertificate=yes`. Enter credentials locally; never commit `.env` or paste a password into chat. DBeaver must be configured by the user.

## Database conflict policy

The setup-only path stops if the derived database already exists. It never drops a database. Bronze tables are never truncated or dropped. A source path and content hash already recorded as successful is skipped; changed content is represented by a later load ID.
