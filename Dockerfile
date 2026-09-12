FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ARD_DATA_DIR=/app/runtime
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock && useradd --create-home --uid 10001 ard
COPY pyproject.toml README.md LICENSE ./
COPY ard ./ard
RUN pip install --no-cache-dir --no-deps . && mkdir -p /app/runtime && chown -R ard:ard /app/runtime
USER ard
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)"
CMD ["python", "-m", "ard", "--host", "0.0.0.0"]
