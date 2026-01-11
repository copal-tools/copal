# Copal-VX (Version Exchange)

**Copal-VX** is a specialized Asset Management System designed to handle the heavy lifting of modern media pipelines. Unlike standard Git (which chokes on large binaries) or raw SMB shares (which lack versioning), Copal-VX combines the speed of tiered object storage with the safety of semantic versioning.

## Architecture


* **Data Plane:** SeaweedFS Filer (Port 8888) handles chunking, deduplication, and storage on mixed SSD/HDD tiers.
* **Control Plane:** A FastAPI service manages project manifests, version tags, and access control.
* **Client:** Lightweight Python tools for "Push" (Upload) and "Pull" (Checkout) workflows.

## Quick Start
1.  **Server:** `cd server && docker-compose up -d`
2.  **Client:** `cd client && uv sync`
