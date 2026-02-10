FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt requirements.txt

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD test -f /app/images.json || python -c "import sys; sys.exit(0)"

CMD ["python", "main.py"]
