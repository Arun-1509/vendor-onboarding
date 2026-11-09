FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Generate sample PDFs once at build time so the "Try a prepared vendor" menu works on a fresh deploy.
RUN python scripts/make_samples.py
ENV VAR_DIR=/data
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
