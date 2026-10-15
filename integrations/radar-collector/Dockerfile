FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY collector.py /app/collector.py
COPY delivery.py /app/delivery.py
CMD ["python", "collector.py"]
