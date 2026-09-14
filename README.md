# TakaTaka Logistics Routing (Flask)

Web app for uploading Excel orders and dispatching routes to Wialon Logistics.

## Local setup

```powershell
cd "path\to\TakaTaka-Logistics-Routing"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env with your credentials
python scripts/generate_hero_gif.py
python app.py
```

Open http://127.0.0.1:5000

## Environment variables

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Flask session signing key |
| `APP_USERNAME` | Login username |
| `APP_PASSWORD` | Login password |
| `WIALON_TOKEN` | Wialon API token (server-side only) |
| `WIALON_RESOURCE_ID` | Wialon resource ID |

Copy `.env.example` to `.env` for local development, or run `vercel env pull .env.local` and copy values into `.env`. Never commit `.env`, `.env.local`, or `.env.development.local`.

## Deploy to Vercel

1. Push this repo to GitHub: [StephenMulingwa/TakaTaka-Logistics-Routing](https://github.com/StephenMulingwa/TakaTaka-Logistics-Routing)
2. Import the project at [vercel.com/new](https://vercel.com/new)
3. In **Project Settings → Environment Variables**, add all variables from `.env.example`
4. Deploy — Vercel auto-detects Flask from `app.py`

Ensure `Monday.zip`–`Saturday.zip` and `Takataka Ids.xlsx` are committed for weekly dispatch.

## Notes

- Wialon dispatch may take up to 60 seconds; increase `maxDuration` in `vercel.json` on Pro if needed.
- Legacy Streamlit files (`main.py`, `test.py`, etc.) remain for reference but are excluded from the Vercel bundle.
