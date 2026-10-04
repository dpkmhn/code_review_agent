#!/bin/sh
set -eu
psql --username postgres --dbname code_reviews --set=ON_ERROR_STOP=1 --set=review_password="$REVIEW_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE review_user LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'review_password') \gexec
ALTER DATABASE code_reviews OWNER TO review_user;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO review_user;
SQL
