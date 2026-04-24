# Tradsiee: Technical Documentation (GEMINI.md)

This file serves as the technical source of truth for the Tradsiee project, prioritized by Gemini CLI.

## Project Overview
Tradsiee is a video-first lead generation platform designed to bridge the gap between customers and tradespeople. By providing visual context through video, it eliminates "discovery trips" and ensures tradies arrive prepared with the right tools and parts.

## Core Tech Stack
- **API Framework:** FastAPI (Python)
- **Persistence & Auth:** Supabase (PostgreSQL + GoTrue)
- **Media Management:** Cloudinary (Server-side signed uploads via FFmpeg pipeline)
- **Communications:** Twilio SMS API
- **UI/UX:** Tailwind CSS (CDN), Vanilla JavaScript, Chart.js

## Backend Architecture (Modular Engine)
The backend is architected for scalability and maintainability, split into specialized modules:

1.  **Entry Point (`app/main.py`)**: Lightweight orchestration. Initializes the FastAPI app, manages the template cache lifespan, and plugs in the API routers.
2.  **Core Brain (`app/core/`)**:
    *   `config.py`: Centralized configuration, environment variable resolution, and shared service clients (Supabase, Twilio, Cloudinary).
    *   `dependencies.py`: Reusable logic including `get_current_user` (Auth Guard), `run_sync` (performance), and rate-limiting.
    *   `video_processor.py`: Asynchronous video engine using FFmpeg to optimize uploads to 1080p before cloud persistence.
3.  **API Routers (`app/api/`)**:
    *   `auth.py`: Identity management, MFA (SMS), and profile security updates.
    *   `leads.py`: Lead ingestion pipeline and pipeline retrieval.
    *   `admin.py`: High-friction "Danger Zone" logic, account deletion, and credit management.
    *   `pages.py`: Asset serving and dynamic JavaScript (widget loader) generation.

## UI/UX Standards
- **Settings Dashboard**: Uses a "Split-Section" layout (1/3 Info | 2/3 Action) to separate context from controls.
- **Interactive Modals**: Sensitive edits (Email, Password, Profile) are performed within focused modals to maintain a clean dashboard state.
- **Security Protocols**:
    *   Email/Password updates require verification of the current password.
    *   Account Deletion (Danger Zone) requires an irreversible checkbox, a slug-based confirmation phrase, and an 8-digit SMS OTP.

## System Workflow
1. **The Widget**: A client-side script injected into trade websites via `loader.js`.
2. **Lead Submission**: Customers provide metadata and a video file. The video is streamed to the Tradsiee server for processing.
3. **Data Pipeline**:
   - **Ingestion**: Raw video is stored temporarily on the server.
   - **Optimization**: `video_processor.py` transcodes the video to 1080p (libx264) to ensure consistency and reduce bandwidth.
   - **Persistence**: The optimized video is uploaded to Cloudinary; metadata is saved to the Supabase `leads` table.
   - **Notification**: Background tasks dispatch SMS alerts to the tradie and confirmation to the customer only after the video is ready.
4. **The Portal**: A protected SPA-style interface (`web/templates/portal.html`) for lead management.

## Setup & Execution
- **Environment**: Requires `.env` in the root with `SUPABASE_URL`, `SUPABASE_KEY` (Service Role), `CLOUDINARY_URL`, and Twilio credentials.
- **Backend Start**: `uvicorn app.main:app --reload` (run from root).
- **Frontend Start**: Static file hosting on port 8000 (standard FastAPI port).

## Roadmap
- [ ] Comprehensive test suite for modular API routes.
- [ ] Transition to a modern framework (e.g., React/Vue) for the dashboard.
- [x] Server-side video optimization and compression (1080p).
- [ ] Automated error tracking integration (e.g., Sentry).

---
*Note: This file is for developer reference. See README.md for the general project overview.*
