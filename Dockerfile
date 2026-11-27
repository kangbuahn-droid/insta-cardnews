FROM mcr.microsoft.com/playwright/python:v1.50.0-noble
ENV PYTHONUNBUFFERED=1 TZ=Asia/Seoul
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk tzdata ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py", "run"]
