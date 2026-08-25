# Python 3.12 for Dokploy; local dev runs fine on 3.10+
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends glpk-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY static/ static/
COPY models/ models/

# Ensure results dir exists even if empty (not copied without .gitkeep)
RUN mkdir -p results

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
