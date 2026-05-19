FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY enrich.py db.py main.py schema.sql ./

ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
