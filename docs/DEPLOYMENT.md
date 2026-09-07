# APEX FLOW — Deployment Guide

> Deploy APEX FLOW to any cloud platform and access it from any device worldwide.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Option 1: Render (Recommended)](#option-1-render-recommended)
- [Option 2: Railway](#option-2-railway)
- [Option 3: Fly.io](#option-3-flyio)
- [Option 4: VPS / Self-Hosted](#option-4-vps--self-hosted)
- [Production Checklist](#production-checklist)
- [PostgreSQL Setup](#postgresql-setup)
- [Custom Domain & HTTPS](#custom-domain--https)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

- A GitHub account with the APEX FLOW repository pushed to it
- A cloud provider account (Render / Railway / Fly.io — all have free tiers)
- Your GitHub Personal Access Token to push code (if not already set up)

---

## Option 1: Render (Recommended)

Render is the easiest deployment path. Free tier supports production-ready HTTPS.

### Step 1: Push your code to GitHub

```bash
git push origin main
```

### Step 2: Create a Render account

Go to [render.com](https://render.com) and sign up (use GitHub OAuth for convenience).

### Step 3: Create a new Web Service

1. Click **New** → **Web Service**
2. Connect your GitHub account and select `nandan503/Apex-Flow`
3. Configure:

| Setting | Value |
|---|---|
| **Name** | `apex-flow` |
| **Region** | Asia (Singapore) or closest to your users |
| **Branch** | `main` |
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `gunicorn backend.app:app` |
| **Instance Type** | Free (or Starter for always-on) |

### Step 4: Set Environment Variables

In the Render dashboard → **Environment** tab, add:

```
SECRET_KEY=<generate with: python3 -c "import secrets; print(secrets.token_hex(32))">
FLASK_ENV=production
ALLOWED_ORIGINS=https://apex-flow-xxxx.onrender.com
SEED_ADMIN_PASSWORD=your_strong_admin_password
SEED_MANAGER_PASSWORD=your_strong_manager_password
SEED_DRIVER_PASSWORD=your_strong_driver_password
SEED_CUSTOMER_PASSWORD=your_strong_customer_password
```

> **PostgreSQL (Optional but Recommended):** Click **New** → **PostgreSQL** in Render. Copy the "Internal Database URL" and add it as `DATABASE_URL` environment variable.

### Step 5: Deploy

Click **Create Web Service**. Render will:
1. Clone your repository
2. Install dependencies from `requirements.txt`
3. Start the app with `gunicorn backend.app:app`

Your app URL will be: `https://apex-flow-xxxx.onrender.com`

**First Run:** The database initializes automatically on first boot.

### Auto-Deploy on Push

Render auto-deploys every time you push to `main`. To disable:
Render Dashboard → Settings → **Auto-Deploy** → Off.

---

## Option 2: Railway

### Step 1: Install Railway CLI

```bash
npm install -g @railway/cli
```

### Step 2: Login and initialize

```bash
railway login
cd /path/to/Apex-Flow
railway init
```

### Step 3: Deploy

```bash
railway up
```

### Step 4: Set environment variables

```bash
railway variables set SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
railway variables set FLASK_ENV=production
railway variables set ALLOWED_ORIGINS=https://your-app.up.railway.app
railway variables set SEED_ADMIN_PASSWORD=your_strong_password
```

### Step 5: Add PostgreSQL (Recommended)

In Railway dashboard → **New** → **Database** → **PostgreSQL**. Railway automatically injects `DATABASE_URL` into your service.

---

## Option 3: Fly.io

### Step 1: Install Fly CLI

```bash
# macOS
brew install flyctl
```

### Step 2: Login and launch

```bash
fly auth login
fly launch
```

Follow the interactive prompts. Fly will detect the `Procfile` automatically.

### Step 3: Set secrets

```bash
fly secrets set SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
fly secrets set FLASK_ENV=production
fly secrets set ALLOWED_ORIGINS=https://apex-flow.fly.dev
```

### Step 4: Deploy

```bash
fly deploy
```

---

## Option 4: VPS / Self-Hosted

For an Ubuntu/Debian VPS (DigitalOcean, AWS EC2, Hetzner, etc.):

### Step 1: Install system dependencies

```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx
```

### Step 2: Clone and install app

```bash
git clone https://github.com/nandan503/Apex-Flow.git /opt/apexflow
cd /opt/apexflow
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 3: Create environment file

```bash
cp .env.example .env
nano .env  # Fill in all required values
```

### Step 4: Create systemd service

```bash
sudo nano /etc/systemd/system/apexflow.service
```

```ini
[Unit]
Description=APEX FLOW Gunicorn Service
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/apexflow
EnvironmentFile=/opt/apexflow/.env
ExecStart=/opt/apexflow/venv/bin/gunicorn backend.app:app --workers 4 --bind 0.0.0.0:5050
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable apexflow
sudo systemctl start apexflow
```

### Step 5: Configure Nginx reverse proxy

```bash
sudo nano /etc/nginx/sites-available/apexflow
```

```nginx
server {
    server_name apex-flow.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:5050;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/apexflow /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### Step 6: Enable HTTPS with Let's Encrypt

```bash
sudo certbot --nginx -d apex-flow.yourdomain.com
```

Certbot will automatically configure HTTPS and redirect HTTP → HTTPS.

---

## Production Checklist

Before going live, verify every item:

- [ ] `SECRET_KEY` is set to a random 64-char hex string (not the default)
- [ ] `FLASK_ENV=production` is set
- [ ] `ALLOWED_ORIGINS` matches your exact deployed URL (no trailing slash)
- [ ] `SEED_*_PASSWORD` values are strong (12+ chars, not default values)
- [ ] `DATABASE_URL` points to PostgreSQL (not SQLite for multi-instance)
- [ ] `REDIS_URL` is set if running multiple Gunicorn workers
- [ ] HTTPS is enforced (Render/Railway do this automatically)
- [ ] `.env` file is NOT committed to git (check `.gitignore`)
- [ ] Default seed credentials have been changed after first DB initialization

---

## PostgreSQL Setup

For production workloads, use PostgreSQL instead of SQLite.

### On Render:
1. Dashboard → **New** → **PostgreSQL**
2. Copy **Internal Database URL**
3. Add as `DATABASE_URL` environment variable on your Web Service

### Connection URL format:
```
postgresql://username:password@hostname:5432/database_name
```

APEX FLOW's `database.py` automatically uses PostgreSQL when `DATABASE_URL` is set, with full SQLite API compatibility via wrapper classes.

---

## Custom Domain & HTTPS

### Render:
1. Dashboard → Settings → **Custom Domains**
2. Add your domain: `apex-flow.yourdomain.com`
3. Copy the CNAME record Render provides
4. Add the CNAME in your DNS provider (Namecheap, Cloudflare, GoDaddy)
5. Wait for DNS propagation (up to 48 hours)
6. Render issues a free Let's Encrypt TLS certificate automatically

### Update ALLOWED_ORIGINS:
After adding a custom domain, update the `ALLOWED_ORIGINS` environment variable:
```
ALLOWED_ORIGINS=https://apex-flow.yourdomain.com
```

---

## Troubleshooting

### App shows 500 error on first boot
- Check logs: Render Dashboard → **Logs** tab
- Usually caused by missing `SECRET_KEY` environment variable
- Check that all required env vars are set

### "could not connect to PostgreSQL" in logs
- Verify `DATABASE_URL` is set correctly
- The app automatically falls back to SQLite if PostgreSQL is unavailable

### CORS errors in browser console
- Ensure `ALLOWED_ORIGINS` exactly matches your frontend URL including `https://`
- No trailing slash: `https://apex-flow.onrender.com` ✅ not `https://apex-flow.onrender.com/` ❌

### Login fails immediately
- Session cookies require HTTPS in production (`SESSION_COOKIE_SECURE=True`)
- Ensure your deployment has a valid TLS certificate

### Rate limit hit (429 errors)
- Login rate limit: 10/min per IP
- Wait 60 seconds and try again
- For testing, use `FLASK_ENV=development` which uses relaxed limits
