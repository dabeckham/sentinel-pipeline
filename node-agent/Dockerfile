FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# nvidia-smi (for GPU probing) is bind-mounted in from the host at runtime,
# same pattern the orchestrator uses for its metrics endpoint.
CMD ["python", "-m", "agent.main"]
