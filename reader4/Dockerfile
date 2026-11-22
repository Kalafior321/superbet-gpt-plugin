FROM python:3.12-slim
WORKDIR /app
COPY reader4/requirements.txt ./requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install --with-deps chromium
COPY reader4/ /app/
ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
