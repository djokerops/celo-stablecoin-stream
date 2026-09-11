FROM python:3.11-slim
WORKDIR /app

# pipenv installs the locked deps into the container's system Python
RUN pip install --no-cache-dir pipenv

# Copy dependency manifests first so Docker can cache the install layer
COPY Pipfile Pipfile.lock* ./
RUN pipenv install --system --deploy --ignore-pipfile || pipenv install --system --skip-lock

COPY main.py .

# uvicorn serves the FastAPI app on 0.0.0.0:8000 inside the container
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]