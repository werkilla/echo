FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY echo/ echo/
COPY pwa/ pwa/

EXPOSE 7338
VOLUME /config

CMD ["uvicorn", "echo.main:app", "--host", "0.0.0.0", "--port", "7338"]
