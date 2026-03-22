FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY weather_poller/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy ingestion package structure
COPY __init__.py /app/ingestion/__init__.py
COPY shared/ /app/ingestion/shared/
COPY static/ /app/ingestion/static/

# Copy poller code
COPY weather_poller/ /app/ingestion/weather_poller/

CMD ["python", "-m", "ingestion.weather_poller.main"]
