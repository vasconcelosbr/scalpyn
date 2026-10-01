FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 coreutils && rm -rf /var/lib/apt/lists/*
COPY pump_ml/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY pump_ml/ /app/pump_ml/
COPY backend/app/services/pump_opportunity_engine.py backend/app/services/pump_contracts.py backend/app/services/pump_ml_research.py /app/app/services/
# Includes process startup: TERM at895s, forced KILL after5s; no permanent worker.
CMD ["timeout","--signal=TERM","--kill-after=5s","895s","python","-m","pump_ml.job"]
