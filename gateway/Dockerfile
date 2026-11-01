FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app:/app/text-risk
COPY text-risk/requirements.txt text-risk/requirements.txt
COPY gateway/requirements.txt gateway/requirements.txt
RUN pip install --no-cache-dir -r gateway/requirements.txt && useradd --uid 10001 --create-home sentinel
COPY gateway gateway
COPY ato ato
COPY text-risk/riskclf text-risk/riskclf
COPY text-risk/models text-risk/models
COPY serving/model serving/model
USER 10001
EXPOSE 18040
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "18040", "--workers", "1"]
