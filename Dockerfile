FROM python:3.12-slim

WORKDIR /app

# Install deps before copying source — layer is cached unless requirements change
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

# Default command runs the API; override in docker-compose / Fly processes to
# run a Celery worker (`celery -A app.celery_app worker -l info`) or beat
# (`celery -A app.celery_app beat -l info`) from the same image.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
