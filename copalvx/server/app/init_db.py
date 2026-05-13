import os
import sys
import argparse
from sqlalchemy import create_engine, text

# --- CONFIGURATION ---
DEFAULT_URL = "postgresql://admin:CHANGE_ME_IN_DOT_ENV@192.168.1.100:5432/asset_system"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_URL)

CLEAN_SLATE_SQL = """
DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS project_files;
DROP TABLE IF EXISTS commits;
DROP TABLE IF EXISTS assets;
DROP TABLE IF EXISTS projects;
"""

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_hash CHAR(64) NOT NULL UNIQUE,
    size_bytes BIGINT NOT NULL,
    mime_type TEXT,
    seaweed_fid TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_assets_hash ON assets(file_hash);

CREATE TABLE IF NOT EXISTS commits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    version_tag TEXT NOT NULL,
    message TEXT,
    author_name TEXT NOT NULL,
    seconds_spent INTEGER DEFAULT 0,
    parent_commit_id UUID REFERENCES commits(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (project_id, version_tag)
);
CREATE INDEX IF NOT EXISTS idx_commits_project_version ON commits(project_id, version_tag);
CREATE INDEX IF NOT EXISTS idx_commits_project_created ON commits(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS project_files (
    commit_id UUID REFERENCES commits(id) ON DELETE CASCADE,
    asset_id UUID REFERENCES assets(id),
    file_path TEXT NOT NULL,
    PRIMARY KEY (commit_id, file_path)
);
CREATE INDEX IF NOT EXISTS idx_project_files_commit ON project_files(commit_id);

CREATE TABLE IF NOT EXISTS events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('push', 'pull')),
    version_tag TEXT NOT NULL,
    user_name TEXT NOT NULL,
    client_host TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_events_project_created ON events(project_id, created_at DESC);
"""


def init_database(clean_slate=False):
    try:
        print("🔌 Connecting to database...")
        engine = create_engine(DATABASE_URL)

        with engine.connect() as conn:
            if clean_slate:
                print("🧹 CLEAN SLATE: Dropping all tables...")
                conn.execute(text(CLEAN_SLATE_SQL))

            print("🏗️  Creating schema (IF NOT EXISTS — safe to re-run)...")
            conn.execute(text(SCHEMA_SQL))

            conn.commit()
            print("✅ Database initialized successfully!")

    except Exception as e:
        print(f"❌ Error: {e}")
        print("Tip: If running locally, check your IP. If in Docker, check DATABASE_URL.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialize CopalVX database schema.")
    parser.add_argument(
        "--clean-slate", action="store_true",
        help="DROP ALL TABLES before creating schema. DESTROYS ALL DATA.",
    )
    args = parser.parse_args()

    if args.clean_slate:
        print("⚠️  WARNING: --clean-slate will DELETE ALL projects, commits, and asset records.")
        print("   SeaweedFS blobs will be orphaned and unrecoverable without a backup.")
        confirm = input("   Type 'DELETE EVERYTHING' to proceed: ")
        if confirm.strip() != "DELETE EVERYTHING":
            print("Aborted.")
            sys.exit(1)

    init_database(clean_slate=args.clean_slate)
