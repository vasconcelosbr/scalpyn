FROM python:3.11-slim

RUN pip install --no-cache-dir \
    mlflow==2.13.0 \
    psycopg2-binary==2.9.9 \
    google-cloud-storage==2.17.0 \
    gunicorn==22.0.0

# MLflow server expõe na porta 8080 (padrão Cloud Run)
ENV PORT=8080

CMD mlflow server \
    --backend-store-uri $MLFLOW_TRACKING_URI \
    --default-artifact-root $MLFLOW_ARTIFACT_ROOT \
    --host 0.0.0.0 \
    --port $PORT \
    --workers 2
