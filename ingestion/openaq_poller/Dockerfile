FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY openaq_poller/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy ingestion package structure
COPY __init__.py /app/ingestion/__init__.py
COPY shared/ /app/ingestion/shared/
COPY static/ /app/ingestion/static/

# Copy poller code
COPY openaq_poller/ /app/ingestion/openaq_poller/

CMD ["python", "-m", "ingestion.openaq_poller.main"]