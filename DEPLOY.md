# Deploying SquintDesk (Flask) on Render

## “Blueprint file render.yaml not found on main branch”

Render reads **`render.yaml` from the Git branch you select** (usually `main`). This error almost always means the file is **not on GitHub/GitLab at the repo root** for that branch, or you pointed Render at the **wrong repository**.

1. **Confirm on the host’s website** (e.g. GitHub → your repo → branch `main`): you should see **`render.yaml`** in the **top level** of the repo (same folder as `requirements.txt`), not inside a subfolder.
2. **Push from your machine** if the file only exists locally:
   ```bash
   git add render.yaml
   git commit -m "Add Render blueprint"
   git push origin main
   ```
3. **Monorepo:** If the Flask app lives in a subfolder (e.g. `apps/scanner/`), either move `render.yaml` to that subfolder and set **Root Directory** in Render to match, or put `render.yaml` at the repo root and set `rootDir` in the YAML (see [Blueprint spec](https://render.com/docs/blueprint-spec)).
4. **Branch names with `/`** (e.g. `feature/deploy`) can break Blueprint discovery on Render; use a branch without slashes or deploy from `main`.
5. **Skip Blueprint:** use **New → Web Service** (not Blueprint), same build/start commands as in `render.yaml` — no `render.yaml` required.

## Prerequisites

- Git repository pushed to GitHub/GitLab/Bitbucket.
- A Render account ([render.com](https://render.com)).

## One-click style (Blueprint)

1. In Render: **New** → **Blueprint** → connect the repo and select `render.yaml`.
2. Confirm the service name and region, then **Apply**.

On first boot, if `config.yml` is not present (it is usually gitignored), the server copies **`config.fno.quick.yml`** → `config.yml` so the app starts with a small symbol list.

## Manual Web Service

1. **New** → **Web Service** → connect the repo.
2. **Runtime:** Python.
3. **Build command:** `pip install -r requirements.txt`
4. **Start command:** `bash bin/start_web.sh`
5. **Instance type:** Free (or paid for more RAM/CPU).

Render sets `PORT`; `gunicorn` binds using that variable.

## Your own `config.yml`

`config.yml` is listed in `.gitignore` so secrets and large symbol lists stay off Git.

**Option A — Secret File (recommended)**  
Dashboard → your service → **Environment** → **Secret Files** → add path `config.yml` and paste your full YAML.

**Option B — Environment path**  
Upload the file to a path the runtime can read, then set:

- `CONFIG_PATH` = absolute path to that file (if your host supports it).

The app loads config via `CONFIG_FILE` in `web_app.py` (`CONFIG_PATH` env or default `config.yml` in the working directory).

## Health check

`render.yaml` sets `healthCheckPath: /` so Render hits the dashboard root. If you change routes, update the health check in the Render dashboard.

## Limits (free tier)

- **Cold starts** after idle.
- **Memory:** heavy `yfinance` / pandas scans may OOM; reduce symbol lists in `config.yml` or upgrade the instance.
- **Timeouts:** `gunicorn` timeout is 120s in `bin/start_web.sh`; increase if long scans time out.

## Local production-like run

```bash
pip install -r requirements.txt
bash bin/start_web.sh
```

On Windows without bash, use WSL or: `gunicorn web_app:app --bind 127.0.0.1:5000` after placing a `config.yml` beside `web_app.py`.
