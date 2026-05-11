# Setup Guide

This guide explains how to run the Payroll Anomaly Detector locally in under 5 minutes.

---

# 1. Clone Repository


git clone https://github.com/AnkitByteForge/payroll-anomaly-detector.git

cd payroll-anomaly-detector


---

# 2. Create Virtual Environment

```bash
python -m venv venv


---

# 3. Activate Virtual Environment

## Windows


venv\Scripts\activate


## Mac/Linux


source venv/bin/activate


---

# 4. Install Backend Dependencies


pip install -r requirements.txt


---

# 5. Configure Environment Variables

Create a `.env` file inside:


backend/


Copy the following contents from `.env.example`:


ERPNEXT_BASE_URL=https://yoursite.erpnext.com
ERPNEXT_API_KEY=abc123...
ERPNEXT_API_SECRET=xyz789...

GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile

ANOMALY_THRESHOLD_PCT=15.0

APP_VERSION=1.0.0
APP_ENV=development


---

# 6. Run Backend


cd backend

uvicorn main:app --reload


Backend runs on:


http://localhost:8000


---

# 7. Run Frontend

Open a new terminal:


cd frontend

npm install

npm run dev


Frontend runs on:


http://localhost:5173


---

# 8. Verify Backend Health

Open:

http://localhost:8000/api/v1/health
```

Expected response:


{
  "service": "Payroll Anomaly Detector",
  "status": "ok"
}


---

# 9. Example Workflow

1. Open the frontend application
2. Enter a payroll review request
3. Run payroll review
4. Review detected anomalies
5. Approve or cancel the HITL checkpoint
6. Generate the final audit report

---

# Example Prompt


Review this month's payroll data and flag anything unusual.
```

---

# API Endpoints

| Endpoint          | Method | Purpose                  |
| ----------------- | ------ | ------------------------ |
| `/api/v1/health`  | GET    | Health check             |
| `/api/v1/run`     | POST   | Start payroll audit      |
| `/api/v1/confirm` | POST   | Confirm or cancel HITL   |
| `/api/v1/history` | GET    | Retrieve session history |

---

# Notes

* Frontend communicates only with the FastAPI backend.
* No direct ERPNext calls are made from the frontend.
* Payroll analysis logic is deterministic.
* Agno coordinates specialist agents for modular analysis.
