FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_ENV=production PORT=5050
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt && useradd --uid 10001 --create-home app
COPY backend backend
COPY frontend frontend
COPY migrations migrations
USER 10001
EXPOSE 5050
CMD ["sh", "-c", "exec gunicorn 'backend.app:create_app()' --bind 0.0.0.0:${PORT:-5050} --workers ${WEB_CONCURRENCY:-2} --timeout 30"]
