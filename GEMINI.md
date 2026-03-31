# Tradsiee: Technical Documentation (GEMINI.md)

This file serves as the technical source of truth for the Tradsiee project, prioritized by Gemini CLI.

## Project Overview
Tradsiee is a video-first lead generation platform designed to bridge the gap between customers and tradespeople. By providing visual context through video, it eliminates "discovery trips" and ensures tradies arrive prepared with the right tools and parts.

## Core Tech Stack
- **API Framework:** FastAPI (Python)
- **Persistence & Auth:** Supabase (PostgreSQL + GoTrue)
- **Media Management:** Cloudinary (Unsigned client-side uploads)
- **Communications:** Twilio SMS API
- **UI/UX:** Tailwind CSS (CDN), Vanilla JavaScript, Chart.js

## System Workflow
1. **The Widget:** A client-side script injected into trade websites.
2. **Lead Submission:** Customers provide a phone number and a video file.
3. **Data Pipeline:**
   - Video → Cloudinary (Direct Upload)
   - Lead Metadata → Supabase `leads` table
   - SMS Alert → Tradie (Twilio)
4. **The Portal:** A protected interface (`portal.html`) where tradies manage leads and view analytics.

## Setup & Execution
- **Environment:** Requires `.env` with `SUPABASE_URL`, `SUPABASE_KEY`, `CLOUDINARY_URL`, and `TWILIO_SID/AUTH`.
- **Backend Start:** `uvicorn main:app --reload`
- **Frontend Start:** Static file hosting on port 5500 (default for many dev servers).

## Code Conventions
- **Validation:** Pydantic models in `main.py`.
- **Security:** `HTTPBearer` for JWT verification against Supabase.
- **Styling:** Rapid UI development using Utility-first CSS (Tailwind).

## Roadmap
- [ ] Comprehensive test suite for FastAPI routes.
- [ ] Transition to a modern framework (e.g., React/Vue) for the dashboard.
- [ ] Enhanced video compression before upload.
- [ ] Automated error tracking integration.

---
*Note: This file is for developer reference. See README.md for the general project overview.*
