# Tradsiee: Technical Documentation (GEMINI.md)

This file serves as the technical source of truth for the Tradsiee project, prioritized by Gemini CLI.

## Project Overview
Tradsiee is a video-first lead generation platform designed to bridge the gap between customers and tradespeople. By providing visual context through video, it eliminates "discovery trips" and ensures tradies arrive prepared with the right tools and parts.

## Core Tech Stack
- **API Framework:** FastAPI (Python)
- **Persistence & Auth:** Supabase (PostgreSQL + GoTrue)
- **Media Management:** Cloudinary (Unsigned client-side uploads)
- **Communications:** Twilio SMS API + Resend SMTP (Transactional Email)
- **UI/UX:** Tailwind CSS (CDN), Vanilla JavaScript, Chart.js

## System Workflow
1. **The Widget:** A client-side script injected into trade websites.
2. **Lead Submission:** Customers provide a phone number and a video file.
3. **Data Pipeline:**
   - Video → Cloudinary (Direct Upload)
   - Lead Metadata → Supabase `leads` table
   - SMS Alert → Tradie (Twilio)
   - Identity Verification → Resend (via Supabase SMTP)
4. **The Portal:** A protected interface (`web/templates/portal.html`) where tradies manage leads and view analytics.
5. **Asset Serving:** `app/main.py` serves assets from the `web/` directory.

## Setup & Execution
- **Environment:** Requires `.env` in the root with `SUPABASE_URL`, `SUPABASE_KEY`, `CLOUDINARY_URL`, etc.
- **Backend Start:** `uvicorn app.main:app --reload` (run from the root directory).
- **Frontend Start:** Static file hosting on port 5500 (pointing to the `web/` directory).

## Code Conventions
- **Modularization:** Logic is split into `app/services`, `app/api`, etc.
- **Validation:** Pydantic models in `app/schemas`.
- **Security:** `HTTPBearer` for JWT verification against Supabase.

## Roadmap
- [ ] Comprehensive test suite for FastAPI routes.
- [ ] Transition to a modern framework (e.g., React/Vue) for the dashboard.
- [ ] Enhanced video compression before upload.
- [ ] Automated error tracking integration.

---
*Note: This file is for developer reference. See README.md for the general project overview.*
