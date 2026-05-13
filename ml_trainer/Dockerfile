FROM python:3.11-slim

WORKDIR /app

# Dependências do sistema
RUN apt-get update && apt-get install -y \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# Dependências Python — só o necessário para treino
COPY ml_trainer/requirements_trainer.txt .
RUN pip install --no-cache-dir -r requirements_trainer.txt

# Código
COPY ml_trainer/ ./ml_trainer/
COPY backend/app/ml/ ./app/ml/

# Cloud SQL Auth Proxy (conexão segura ao Cloud SQL)
RUN apt-get update && apt-get install -y wget && \
    wget -O /cloud-sql-proxy \
    https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.11.0/cloud-sql-proxy.linux.amd64 && \
    chmod +x /cloud-sql-proxy && \
    rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["python", "-m", "ml_trainer.job"]
