-- Sentinel Pipeline — PostgreSQL initialization
-- Note: The full schema is managed by Alembic migrations in orchestrator/alembic/
-- This file only creates extensions needed before Alembic runs.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- for fast text search on class labels / file paths
