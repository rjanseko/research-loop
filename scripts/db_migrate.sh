#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/001_research.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/002_research_attachments.sql

echo "Database migrations applied."
