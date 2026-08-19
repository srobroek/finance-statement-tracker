FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/finance-pdf

RUN groupadd --gid "${APP_GID}" financepdf \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --no-create-home --shell /usr/sbin/nologin financepdf

WORKDIR /opt/finance-pdf
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --requirement requirements.txt
COPY healthcheck.py server.py worker.py ./
COPY tests ./tests

USER financepdf:financepdf
ENTRYPOINT ["python", "/opt/finance-pdf/server.py"]
