FROM python:3.13-slim

WORKDIR /app

# Dependencies first, for Docker layer caching — only reinstalled when
# requirements.txt actually changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code.
COPY detector.py rag.py orchestrator.py metrics.py api.py approvals.py generate_mock_data.py ./
COPY events/ ./events/
COPY data/policies/ ./data/policies/

# Bake in mock billing data and the Chroma policy-doc ingestion at
# build time — the image ends up self-contained, no PVC and no
# startup-time ingestion delay, and the embedding model's one-time
# ONNX download also happens here rather than at runtime.
RUN python generate_mock_data.py && python rag.py

# Never bake OPENAI_API_KEY or any other secret into the image — those
# arrive at runtime via the container's environment (see infra/api/).

RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
