# Render Deployment Setup

## Important Settings for Render Dashboard:

### Root Directory
- **Root Directory:** `backend` (or leave empty if deploying from backend folder directly)

### Build & Start Commands:
- **Build Command:** `chmod +x build.sh && ./build.sh`
- **Start Command:** `gunicorn kattappa.wsgi:application --bind 0.0.0.0:$PORT`

### Environment Variables (Required):
1. **SECRET_KEY** = `p4#o3p-2+2vz_o_gl6iu=o9qf(xal#mn%b3i=1fz#mz6^1=0#*`
2. **DEBUG** = `False`
3. **DATABASE_URL** = Your database Internal Database URL

### Optional:
- **PYTHON_VERSION** = `3.12.0`

## File Structure:
```
your-repo/
  backend/
    Procfile
    build.sh
    manage.py
    requirements.txt
    kattappa/
    quotations/
    ...
```

## Render Dashboard Settings:
- **Name:** synquote-backend
- **Root Directory:** `backend` ⚠️ IMPORTANT!
- **Build Command:** `chmod +x build.sh && ./build.sh`
- **Start Command:** `gunicorn kattappa.wsgi:application --bind 0.0.0.0:$PORT`

