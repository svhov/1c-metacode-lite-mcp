FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy pre-downloaded ONNX model (paraphrase-multilingual-MiniLM-L12-v2)
COPY models/ ./models/

COPY app/ ./app/

EXPOSE 6001

CMD ["python", "-m", "app.main"]
