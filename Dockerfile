FROM python:3.11-slim

WORKDIR /app

COPY requirements-runtime.txt .
RUN pip install --no-cache-dir -r requirements-runtime.txt

COPY app.py circuit_sim.py llm.py judge.py stats.py circuit.json ./
COPY templates ./templates
COPY static ./static

ENV PORT=8080
EXPOSE 8080

CMD ["python", "app.py"]
