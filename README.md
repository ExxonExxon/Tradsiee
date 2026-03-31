# 🛠️ Tradsiee

**The Video Lead Generation Engine for Tradies.**

Tradsiee helps tradespeople understand jobs before they arrive. Customers record a quick video of their issue, and tradies get an instant lead with visual context—saving time, reducing wasted trips, and improving job accuracy.

---

## 🚀 Key Features

- 📹 **Video-First Leads:** High-quality video uploads for clear job context.
- 📲 **Instant Notifications:** Real-time SMS alerts via Twilio.
- 📊 **Tradie Dashboard:** Comprehensive portal for managing leads and business performance.
- 🧩 **Easy Integration:** Simple widget injection for any website.
- 🔐 **Secure & Reliable:** Powered by Supabase Auth and FastAPI.

---

## 🛠️ Tech Stack

- **Backend:** Python ([FastAPI](https://fastapi.tiangolo.com/))
- **Database & Auth:** [Supabase](https://supabase.com/)
- **Video Hosting:** [Cloudinary](https://cloudinary.com/)
- **Messaging:** [Twilio](https://www.twilio.com/)
- **Frontend:** HTML5, Vanilla JS, [Tailwind CSS](https://tailwindcss.com/), [Chart.js](https://www.chartjs.org/)

---

## 🏗️ Architecture

1.  **Widget Injection:** Tradies embed a simple `<script>` tag.
2.  **Customer Flow:** Users input details and record/upload a video.
3.  **Processing:** Video is stored in Cloudinary; metadata is saved in Supabase.
4.  **Notification:** Tradie receives an SMS via Twilio.
5.  **Management:** Tradie views and manages leads via the **Portal**.

---

## 🚦 Getting Started

### Prerequisites
- Python 3.12+
- A `.env` file with Supabase, Cloudinary, and Twilio credentials.

### Installation
1.  **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/tradsiee.git
    cd tradsiee
    ```
2.  **Set up the environment:**
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # MacOS/Linux
    pip install -r requirements.txt
    ```
3.  **Run the backend:**
    ```bash
    uvicorn main:app --reload
    ```
4.  **Serve the frontend:**
    Open the HTML files using a local server (e.g., Live Server in VS Code).

---

## 📂 Project Structure

- `main.py`: FastAPI application & API routes.
- `portal.html`: The Tradie dashboard.
- `index.html`: The lead capture widget.
- `login.html` / `signup.html`: Authentication flow.
- `tasks.py`: Background tasks and helper functions.

---
*Tradsiee — See the job. Save the time.*
