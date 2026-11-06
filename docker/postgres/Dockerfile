FROM postgres:16-bookworm
# Both packages come from the PostgreSQL apt repository the official image
# already trusts. pgvector: PostgreSQL license. pgaudit: PostgreSQL license.
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-16-pgvector postgresql-16-pgaudit \
 && rm -rf /var/lib/apt/lists/*
