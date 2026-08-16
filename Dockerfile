FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot

# База лежит в /app/data. Том монтируется снаружи: в docker-compose.yml — секцией
# volumes, в Railway — через Settings → Volumes (инструкцию VOLUME он не поддерживает).
RUN mkdir -p /app/data

CMD ["python", "-m", "bot"]
