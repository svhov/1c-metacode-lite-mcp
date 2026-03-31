FROM onec-buddy-mcp-1c-code-checker:latest

WORKDIR /app

RUN pip install --no-cache-dir neo4j>=5.0.0

COPY app/ ./app/

CMD ["python", "-m", "app.main"]
