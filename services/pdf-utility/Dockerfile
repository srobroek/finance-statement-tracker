ARG PYTHON_BUILD_IMAGE=cgr.dev/chainguard/python@sha256:57dc165efe7e6ad75386ffc28c4e929c2121f8f3737f24499974c17fec90e8e9
ARG PYTHON_RUNTIME_IMAGE=cgr.dev/chainguard/python@sha256:2c316251a8502a7cb50b0ecf81a894587c743027bba8ace38c3082ce1f4c72c0

FROM ${PYTHON_BUILD_IMAGE} AS build
USER root
WORKDIR /build
COPY requirements.txt ./
RUN python -m venv /venv \
    && /venv/bin/python -m pip install --no-cache-dir --requirement requirements.txt \
    && /venv/bin/python -m pip check \
    && /venv/bin/python -m pip uninstall --yes pip setuptools msgpack \
    && /venv/bin/python -c "import lxml, pikepdf, PIL, pypdf"

FROM ${PYTHON_RUNTIME_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/finance-pdf \
    PATH=/venv/bin:$PATH

WORKDIR /opt/finance-pdf
COPY --from=build /venv /venv
COPY --chown=1000:1000 healthcheck.py server.py worker.py ./
COPY --chown=1000:1000 tests ./tests

USER 1000:1000
ENTRYPOINT ["python", "/opt/finance-pdf/server.py"]
