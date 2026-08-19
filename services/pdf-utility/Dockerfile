FROM public.ecr.aws/docker/library/python:3.12.11-slim-bookworm@sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49

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
