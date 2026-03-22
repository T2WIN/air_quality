FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY openaq_poller/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared code
COPY shared/ /app/shared/
COPY static/ /app/static/
COPY __init__.py /app/__init__.py

# Copy poller code
COPY openaq_poller/ /app/openaq_poller/

CMD ["python", "-m", "ingestion.openaq_poller.main"]