FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY riskclf/ riskclf/
COPY models/ models/
ENV CLASSIFIER_MODE=hybrid \
    LLM_BASE_URL=http://host.docker.internal:8091/v1 \
    LLM_TIMEOUT_S=2.0
EXPOSE 8000
USER nobody
CMD ["uvicorn", "riskclf.service:app", "--host", "0.0.0.0", "--port", "8000"]
