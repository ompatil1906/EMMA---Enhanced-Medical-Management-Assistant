# EMMA Hosting & Deployment Guide

This guide details how to deploy the **EMMA Voice Gateway** (backend) on **Render** and the **Next.js Web UI** (frontend) on **Vercel**.

```
┌─────────────────────────┐               WSS (Secure WebSocket)              ┌─────────────────────────┐
│     Next.js Web UI      │ ────────────────────────────────────────────────> │   EMMA Voice Gateway    │
│    Hosted on Vercel     │                                                   │    Hosted on Render     │
│ (https://*.vercel.app)  │ <──────────────────────────────────────────────── │ (*.onrender.com:8000)   │
└─────────────────────────┘            Echoed Audio & State Events            └─────────────────────────┘
```

---

## Part 1: Deploy Backend on Render

The voice gateway backend is located in [`apps/voice-gateway`](file:///Users/ompatil/Om/Projects/QuantumLoopAI/EMMA---Enhanced-Medical-Management-Assistant/apps/voice-gateway). It is containerized using `uv` and Python 3.13.

### Option A: 1-Click Blueprint (Recommended)

The repository includes a ready-to-use [`render.yaml`](file:///Users/ompatil/Om/Projects/QuantumLoopAI/EMMA---Enhanced-Medical-Management-Assistant/render.yaml) file.

1. Go to your [Render Dashboard](https://dashboard.render.com/).
2. Click **New +** and select **Blueprint**.
3. Connect your GitHub repository: `ompatil1906/EMMA---Enhanced-Medical-Management-Assistant`.
4. Render will detect `render.yaml` and configure:
   - **Service Name:** `emma-voice-gateway`
   - **Runtime:** `Docker`
   - **Root Directory:** `apps/voice-gateway`
   - **Health Check Path:** `/health/live`
   - **Environment Variables:** `APP_ENV=production`, `LOG_LEVEL=INFO`, `EXOTEL_MODE=simulation`, `CORS_ORIGINS=*`
5. Click **Apply**.
6. Once deployed, copy your service's URL (e.g., `https://emma-voice-gateway.onrender.com`).

---

### Option B: Manual Web Service Setup on Render

If you prefer setting up manually without Blueprints:

1. In [Render Dashboard](https://dashboard.render.com/), click **New +** > **Web Service**.
2. Select your repository `EMMA---Enhanced-Medical-Management-Assistant`.
3. Configure the following fields:
   * **Name:** `emma-voice-gateway`
   * **Region:** Select the region closest to you (e.g., Oregon, Ohio, Frankfurt, Singapore).
   * **Branch:** `main`
   * **Root Directory:** `apps/voice-gateway`
   * **Runtime:** `Docker`
   * **Dockerfile Path:** `Dockerfile`
   * **Docker Context:** `.` (or leave default)
   * **Instance Type:** `Free`
4. Expand **Advanced Settings**:
   * **Health Check Path:** `/health/live`
   * **Add Environment Variables:**
     * `APP_ENV`: `production`
     * `LOG_LEVEL`: `INFO`
     * `EXOTEL_MODE`: `simulation`
     * `CORS_ORIGINS`: `*` (or your Vercel URL once known)
     * *(Optional)* `WS_AUTH_TOKEN`: Leave blank unless you want to require authorization tokens for WebSocket sessions.
5. Click **Create Web Service**.

### Verify Backend Health
Once Render finishes building:
* Visit `https://<your-service-name>.onrender.com/health/live` in your browser.
* Expected output:
  ```json
  {"status":"alive","uptime_seconds":...}
  ```
* Visit `https://<your-service-name>.onrender.com/health/ready`.
* Expected output:
  ```json
  {"status":"ready","checks":{"lifespan_complete":true,"provider_mode":"simulation",...}}
  ```

---

## Part 2: Deploy Frontend on Vercel

The Next.js UI is located in [`apps/ui`](file:///Users/ompatil/Om/Projects/QuantumLoopAI/EMMA---Enhanced-Medical-Management-Assistant/apps/ui).

### Step 1: Import Project to Vercel
1. Log in to your [Vercel Dashboard](https://vercel.com/dashboard).
2. Click **Add New…** > **Project**.
3. Import your GitHub repository: `ompatil1906/EMMA---Enhanced-Medical-Management-Assistant`.

### Step 2: Configure Monorepo Root Directory
Because this repository is structured as a monorepo:
1. In the **Configure Project** screen, find **Root Directory**.
2. Click **Edit** and select `apps/ui`.
3. Keep the **Framework Preset** as **Next.js** (Vercel automatically detects this).

### Step 3: Set Environment Variables
In the **Environment Variables** section, add:
* **Key:** `NEXT_PUBLIC_VOICE_GATEWAY_URL`
* **Value:** `wss://<your-render-service-name>.onrender.com/ws/exotel`
  *(Note: Replace `<your-render-service-name>` with your actual Render service hostname. Note the `wss://` secure WebSocket protocol).*

### Step 4: Deploy
1. Click **Deploy**.
2. Vercel will install dependencies via `pnpm` and run `next build`.
3. Once complete, your site will be live at `https://<your-project>.vercel.app`.

---

## Part 3: Verification & End-to-End Testing

1. Open your Vercel frontend URL in Chrome/Edge/Firefox.
2. Grant microphone permissions when prompted.
3. Tap the center voice orb or "Connect" button.
4. Speak a phrase into your microphone (e.g., *"Testing voice gateway echo"*).
5. The audio frames will stream via secure WebSocket (`wss://`) to Render, and the voice gateway will echo back the PCM audio chunks in real-time with latency metrics displayed on screen.

---

## Troubleshooting & Tips

* **Render Free Tier Cold Starts:** Free instances spin down after 15 minutes of inactivity. When first connecting, the first request may take 30-50 seconds to wake up the container.
* **Mixed Content (HTTP vs HTTPS):** Because Vercel serves the UI over HTTPS, browsers require all WebSocket connections to use **`wss://`** (secure WebSocket). Never use `ws://` on production Vercel.
* **CORS:** The backend includes CORS middleware (`CORS_ORIGINS=*`) ensuring any cross-origin HTTP health checks from Vercel or external scripts succeed without restrictions.
