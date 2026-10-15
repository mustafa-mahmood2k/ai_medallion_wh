# Local source data

CSV files in this directory are local inputs and are excluded from Git by default. They were provided from the folders `source_erp/` and `source_crm/` for this project; preserve their original contents locally. Their upstream redistribution terms and attribution were not established, so do not publish the files. Obtain replacements from the authorized source that supplied the originals.

Expected layout (nested folders are supported):

```text
datasets/
  source_erp/*.csv
  source_crm/*.csv
```

The loader derives bronze table names from each relative path under `datasets/`.
