#!/bin/sh
set -eu
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=app_password="$POSTGRES_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE housignal_app LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS', :'app_password') \gexec
SELECT format('CREATE ROLE housignal_worker LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS', :'app_password') \gexec
GRANT USAGE ON SCHEMA public TO housignal_app, housignal_worker;
SQL
