#!/bin/bash
set -euo pipefail
for service in catalog users recommendations; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres --set=service="$service" --set=db_password="$POSTGRES_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'service', :'db_password') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'service', :'service') \gexec
SQL
done
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname catalog -c 'CREATE EXTENSION IF NOT EXISTS vector;'
