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

ENTRYPOINT ["python", "-m", "ml_trainer.job"]
