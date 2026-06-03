FROM python:3.12-slim

WORKDIR /app

# Install deps before copying source — layer is cached unless requirements change
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# /data is where the persistent Fly volume is mounted
RUN mkdir -p /data

EXPOSE 8080

# Single worker — SQLite can't handle concurrent writes across multiple processes
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
