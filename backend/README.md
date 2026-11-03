# FEASTYmap API

## Start the backend

From the repository root in PowerShell:

```powershell
cd backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The API health check is at `http://127.0.0.1:8000/api/health`. Interactive API docs are at `http://127.0.0.1:8000/docs`.

## Start the frontend

In a second terminal, from the repository root:

```powershell
npm run dev
```

The planner posts preferences and the current sample place list to `POST /api/itinerary/generate`. Set `NEXT_PUBLIC_API_URL` in the frontend environment to override the default API origin (`http://127.0.0.1:8000`).

To allow additional frontend origins, set `FRONTEND_ORIGINS` to a comma-separated list before starting Uvicorn.