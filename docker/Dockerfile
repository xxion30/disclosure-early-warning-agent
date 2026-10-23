FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.9.5
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .
ENV PATH="/app/.venv/bin:$PATH"
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
