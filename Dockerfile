FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./server/
COPY shared/ ./shared/

ENV PORT=8080

CMD ["python", "-m", "server.main"]
