import os
import sqlalchemy
from sqlalchemy import create_engine, text

# --- CONFIGURATION ---
# PRIORITY 1: Environment Variable (Docker sets this to the internal "db" host)
# PRIORITY 2: Fallback String (For local testing on laptop with your IP)
# Replace '192.168.178.161' with your actual IP if testing outside Docker
DEFAULT_URL = "postgresql://admin:CHANGE_ME_IN_DOT_ENV@192.168.178.161:5432/asset_system"

# This logic prevents the "Stuck" issue inside Docker
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_URL)

def init_database():
    try:
        print(f"🔌 Connecting to database...")
        # (We don't print the full URL to keep passwords out of logs, but it's using the right one now)
        
        engine = create_engine(DATABASE_URL)

        # SQL to wipe everything (Clean Slate)
        clean_slate_sql = """
        DROP TABLE IF EXISTS project_files;
        DROP TABLE IF EXISTS commits;
        DROP TABLE IF EXISTS assets;
        DROP TABLE IF EXISTS projects;
        """

        # SQL to build everything
        schema_sql = """
        CREATE EXTENSION IF NOT EXISTS "pgcrypto";

        CREATE TABLE projects (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );

        CREATE TABLE assets (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            file_hash CHAR(64) NOT NULL UNIQUE,
            size_bytes BIGINT NOT NULL,
            mime_type TEXT,
            seaweed_fid TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        CREATE INDEX idx_assets_hash ON assets(file_hash);

        CREATE TABLE commits (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
            version_tag TEXT NOT NULL,
            message TEXT,
            author_name TEXT NOT NULL,
            seconds_spent INTEGER DEFAULT 0,
            parent_commit_id UUID REFERENCES commits(id),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );

        CREATE TABLE project_files (
            commit_id UUID REFERENCES commits(id) ON DELETE CASCADE,
            asset_id UUID REFERENCES assets(id),
            file_path TEXT NOT NULL,
            PRIMARY KEY (commit_id, file_path)
        );
        CREATE INDEX idx_project_files_commit ON project_files(commit_id);
        """

        with engine.connect() as conn:
            # 1. WIPE OLD DATA (Enabled now)
            print("🧹 Wiping old tables (Clean Slate)...")
            conn.execute(text(clean_slate_sql))

            # 2. CREATE NEW TABLES
            print("🏗️  Creating new schema...")
            conn.execute(text(schema_sql))
            
            conn.commit()
            print("✅ Database initialized successfully!")

    except Exception as e:
        print(f"❌ Error: {e}")
        print("Tip: If running locally, check your IP. If in Docker, check DATABASE_URL.")

if __name__ == "__main__":
    init_database()
