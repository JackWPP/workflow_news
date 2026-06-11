FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY frontend/ ./frontend/
RUN cd frontend && npm install && npm run build

COPY . .

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8765/api/diagnostics/health || exit 1

CMD ["python", "main.py"]
