FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 `
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends `
    chromium `
    ca-certificates `
    fonts-liberation `
    libnss3 `
    libxss1 `
    libasound2 `
    libxshmfence1 `
    libxi6 `
    libgconf-2-4 `
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY . /app

ENV HEADLESS=true
CMD ["python", "main.py"]
