# AI Tutor — supervised, curriculum-grounded learning pilot

AI Tutor is a web application for a university learning pilot. Administrators upload the programme curriculum, which is
extracted with **Docling** and structured with **Google Gemini**. Faculty review the outcomes, map CLOs to PLOs, approve
teaching sources, questions and lessons, and then activate courses. Students register with their institutional e-mail, enrol,
take an adaptive diagnostic, follow an explained learning path, study accessible material, practise with immediate feedback
and Socratic hints, get retaught when below mastery, track their progress and appeal results. Instructors supervise
everything from a console with reviews, escalations and path control. Every AI response is grounded in approved sources,
answer keys are protected, and all decisions are audited.

## Formative quizzes and the AI teacher debrief

- **10-question formative quizzes** drawn only from the teacher-approved item bank: multiple choice plus 3–5 written
  questions (true/false, fill in the blank, short answer). Every question counts, and answer keys stay encrypted.
- **Marks are shown only after the quiz is submitted.** Formative quizzes give no hints, so the score shows what the
  student really knows.
- **Teacher debrief:** after submitting, an AI teacher goes through each wrong answer. It asks what the student was
  thinking, works out why the answer was wrong (concept gap, misreading, careless slip or partly right), explains the
  idea again and asks one short check question. What it learns feeds into **reteaching**, so the next explanation
  starts from the student's actual misunderstanding.
- **Practice** gives Socratic hints and one practice set per lecture. Both tutors (the topic tutor and the docked
  *Ask the tutor* rail) nudge the student towards the answer instead of giving it away.

## Tech stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.13, FastAPI, SQLAlchemy 2, Pydantic, Uvicorn (HTTPS) |
| Database | PostgreSQL via `DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/aitutor` (the SQLite file `backend/data/aitutor.db` still works for quick local runs). Move SQLite data across once with `python -m app.cli copy-from-sqlite data/aitutor.db`. Tests use in-memory SQLite, or PostgreSQL when `TEST_DATABASE_URL` points at an empty throwaway database |
| AI | Google Gemini (`google-genai`) via one governed gateway; deterministic offline fallback |
| Document extraction | Docling (PDF, DOCX, PPTX, HTML), BM25 retrieval over approved sources |
| Security | Argon2 passwords, JWT + rotating refresh cookies, AES-256-GCM field/file encryption, separate key ring, encrypted backups, hash-chained audit log |
| Frontend | React 19, TypeScript, Vite 6, React Router, react-markdown + KaTeX + highlight.js + Mermaid |
| Reports | ReportLab (PDF), CSV |
| Tests | pytest (backend), Vitest + Testing Library (frontend), end-to-end smoke script |

## Folder structure

```
AI-Tutor/
├─ backend/
│  ├─ app/
│  │  ├─ main.py           FastAPI app, middleware, router list
│  │  ├─ config.py         settings from backend/.env
│  │  ├─ db.py             engine / session
│  │  ├─ cli.py            init-keys, gen-cert, seed, backup, restore, rotate-keys, create-user
│  │  ├─ seed.py           demo data built through the real API
│  │  ├─ core/             crypto, security, rbac, audit, backup, tls, headers
│  │  ├─ models/           database tables (one file per domain)
│  │  ├─ routers/          REST endpoints (one file per feature)
│  │  └─ services/         AI gateway, Gemini client, extraction, parsing, retrieval, mastery,
│  │                       criteria, diagnostic, paths, adaptation, reteach, escalation, items,
│  │                       quiz marking (written answers), practice
│  ├─ tests/               pytest suite (one file per user story) + live Gemini tests
│  ├─ sample_data/         sample curriculum (MD/PDF/DOCX), lecture notes, CS101 question bank
│  ├─ e2e_smoke.py         end-to-end check against the running app
│  ├─ run.py               starts the API (HTTPS when certs exist)
│  ├─ requirements.txt, requirements-docling.txt, .env.example, pytest.ini
├─ frontend/
│  ├─ src/
│  │  ├─ App.tsx, routes.tsx, publicRoutes.tsx   layout and role-based navigation
│  │  ├─ api/  auth/  components/                API client, auth context, shared UI, Markdown, tutor
│  │  ├─ pages/admin/  pages/staff/  pages/student/  pages/auth/
│  │  └─ test/                                     Vitest tests
│  └─ vite.config.ts                              HTTPS dev server + /api proxy
├─ setup.ps1, start-backend.ps1, start-frontend.ps1
└─ .gitignore
```

## Setup (Windows)

Prerequisites: **Python 3.13** and **Node.js 20+** (both on PATH) and Git.

```powershell
cd C:\Users\Hp\OneDrive\Desktop\AI-Tutor
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

`setup.ps1` creates `backend\.venv`, installs the Python packages (and Docling, a large first-time download),
creates `backend\.env` from `.env.example` if missing, generates the encryption key ring and JWT secret
(`python -m app.cli init-keys`), creates a local HTTPS certificate (`gen-cert`), loads demo data (`seed`) and runs `npm install`.

### Configuration (`backend\.env`)

| Variable | Meaning |
|----------|---------|
| `GEMINI_API_KEY` | Your Google AI Studio key. Leave empty to run AI features in offline mode. **Never commit it.** |
| `GEMINI_MODEL` | Default `gemini-3.5-flash-lite` |
| `GEMINI_FALLBACK_MODELS` | Backup models tried when the main one is overloaded (default `gemini-3.1-flash-lite,gemini-3.5-flash,gemini-flash-lite-latest`) |
| `AI_MODE` | `auto` (use Gemini, fall back on error) or `offline` |
| `JWT_SECRET` | Generated by `init-keys` |
| `KEYRING_PATH` | Data-encryption keys, kept apart from the database (default `./keys/keyring.json`) |
| `DATABASE_URL`, `ALLOWED_EMAIL_DOMAIN`, `FRONTEND_URL` | See `.env.example` |

## Running

Open two PowerShell terminals in the project folder:

```powershell
.\start-backend.ps1     # API on https://localhost:8000   (interactive docs: https://localhost:8000/docs)
.\start-frontend.ps1    # Web app on https://localhost:5173
```

Open **https://localhost:5173** and accept the self-signed certificate once. Demo accounts (password **`Demo2026pass`**):

| Role | E-mail | What to try |
|------|--------|-------------|
| Administrator | admin@university.edu | Curriculum upload/review, offerings, criteria, users, audit log, AI governance |
| Instructor | sara.instructor@university.edu | Console (CS101/CS201), review queue, item bank, content, Bilal's escalation, Ayesha's appeal |
| Instructor | omar.instructor@university.edu | MATH110 — activation blocked until mappings are approved |
| Exam staff | exams@university.edu | Read-only audit log and AI traces |
| Student | ayesha.student@university.edu | Learning path, units, practice with hints, formative quiz + teacher debrief, progress PDF, appeals |
| Student | bilal.student@university.edu | Struggling learner (escalated); CS201 blocked by prerequisite |
| Student | chen.student@university.edu | New learner: take the diagnostic |

New students can also self-register (any `@university.edu` address). Verification and password-reset e-mails appear in the
**Demo mailbox** link on the sign-in page.

Useful commands (from `backend`, with `.venv\Scripts\python.exe`):

```powershell
.venv\Scripts\python.exe -m app.cli seed          # reset and reload demo data
.venv\Scripts\python.exe -m app.cli backup        # encrypted backup into backend\backups
.venv\Scripts\python.exe -m app.cli restore FILE  # restore a backup
.venv\Scripts\python.exe -m app.cli rotate-keys   # new encryption key, re-encrypt all data
```

## Running the tests

```powershell
cd backend
.venv\Scripts\python.exe -m pytest              # 342 tests, one file per user story (offline, no network)
.venv\Scripts\python.exe -m pytest -m live      # real Gemini calls (needs a working GEMINI_API_KEY)

cd ..\frontend
npm test                                        # Vitest component tests
npm run build                                   # type-check + production build

# End-to-end against the running app (both servers started, demo data seeded):
cd ..\backend
.venv\Scripts\python.exe e2e_smoke.py
```

## Notes

- Gemini is verified live (`pytest -m live` passes). Temporary Google errors (503 overloaded, 429 rate limit) are retried with
  backoff and then sent to backup models (`GEMINI_FALLBACK_MODELS`); if every model fails, the app uses its labelled offline
  mode. Without a key it runs fully offline; the quiz debrief then still walks through every wrong answer using the
  stored explanation of the right answer.
- Keep the project outside OneDrive or pause sync while developing: syncing `node_modules` and `.venv` is slow.
