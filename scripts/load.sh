#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x "$project_dir/.venv/bin/python" ]]; then
  echo "Run scripts/setup.sh first." >&2
  exit 2
fi

if [[ "${1:-}" == "--preflight" ]]; then
  exec "$project_dir/.venv/bin/python" "$project_dir/src/loader.py" --preflight
fi

container_password=""
if command -v docker >/dev/null 2>&1; then
  container_password="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' sqlserver2025 2>/dev/null | sed -n 's/^MSSQL_SA_PASSWORD=//p' | head -n 1 || true)"
fi
if [[ -n "$container_password" ]]; then
  export MSSQL_PASSWORD="$container_password"
  unset container_password
elif [[ -z "${MSSQL_PASSWORD:-}" ]]; then
  if [[ -t 0 && -t 1 ]]; then
    read -r -s -p 'SQL Server sa password: ' MSSQL_PASSWORD
    echo
    export MSSQL_PASSWORD
  else
    echo "No container password was available and this is not a visible terminal. Run this script in a visible Mac Terminal; it will prompt for the SQL Server password without echoing it." >&2
    exit 2
  fi
fi

# Apply Homebrew OpenSSL compatibility only to this loader process.
if command -v brew >/dev/null 2>&1; then
  openssl_prefix="$(brew --prefix openssl@3 2>/dev/null || true)"
  if [[ -n "$openssl_prefix" && -d "$openssl_prefix/lib" ]]; then
    export DYLD_LIBRARY_PATH="${openssl_prefix}/lib${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"
  fi
fi
exec "$project_dir/.venv/bin/python" "$project_dir/src/loader.py" "$@"
