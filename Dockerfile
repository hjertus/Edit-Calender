FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/

ENV DATA_FILE=/data/calendars.json

VOLUME ["/data"]

EXPOSE 8742

CMD ["python", "app.py"]
