ARG PYTHON_BUILD_IMAGE=cgr.dev/chainguard/python:latest-dev@sha256:afdbadf8d697739ab8e10a4d355d0850daa439cba3e6f0e39a73f7f2d3d839b7
ARG PYTHON_RUNTIME_IMAGE=cgr.dev/chainguard/python:latest@sha256:eca30c0ac647bf28beaec7442388609d14fd100984fa63397e6015eaffe22aa1

FROM ${PYTHON_BUILD_IMAGE} AS build
USER root
WORKDIR /build
COPY requirements.txt ./
RUN python -m venv /venv \
    && /venv/bin/python -m pip install --no-cache-dir --requirement requirements.txt \
    && /venv/bin/python -m pip check \
    && /venv/bin/python -m pip uninstall --yes pip setuptools msgpack \
    && /venv/bin/python -c "import lxml, pikepdf, PIL, pypdf, pdfplumber"

FROM ${PYTHON_RUNTIME_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/platform-pdf \
    PATH=/venv/bin:$PATH

WORKDIR /opt/platform-pdf
COPY --from=build /venv /venv
COPY --chown=1000:1000 healthcheck.py server.py worker.py ./
COPY --chown=1000:1000 tests ./tests

USER 1000:1000
ENTRYPOINT ["python", "/opt/platform-pdf/server.py"]
