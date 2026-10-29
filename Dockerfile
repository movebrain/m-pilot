FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    GRADIO_SERVER_NAME=0.0.0.0 GRADIO_ANALYTICS_ENABLED=False \
    ANONYMIZED_TELEMETRY=False HOME=/tmp
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY app.py app_customer.py app_engineer.py ./
COPY mpilot/ mpilot/
COPY fake/ fake/
COPY scripts/ scripts/
COPY prompts/ prompts/
COPY data/manuals/ data/manuals/
COPY data/errorcodes.csv data/history_seed.csv data/
USER 10001:10001
CMD ["python", "app_customer.py"]
