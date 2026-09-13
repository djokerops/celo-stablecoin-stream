FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY stream_stablecoins.py .
COPY labels.py .
COPY labels/ ./labels/
CMD ["python", "stream_stablecoins.py"]