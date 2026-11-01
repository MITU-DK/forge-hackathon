# One container: builds the UI, serves it from the FastAPI app. Deploy on Render / Railway / Fly / HF Spaces.
FROM node:20-slim AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY --from=ui /ui/dist frontend/dist
WORKDIR /app/backend
ENV PORT=8000
# --proxy-headers: behind Render/Railway the real client IP arrives in X-Forwarded-For; without it every visitor shares one rate-limit bucket
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
