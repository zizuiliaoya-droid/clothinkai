# Synthetic fixture: public runtime lock and healthcheck, not the original API image.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG PIP_INDEX_URL
COPY requirements-linux.lock /opt/requirements-linux.lock
RUN python -m pip --isolated install --index-url "$PIP_INDEX_URL" --no-cache-dir --disable-pip-version-check --timeout 30 --retries 1 --only-binary=:all: --no-deps --require-hashes -r /opt/requirements-linux.lock && python -m pip check
HEALTHCHECK --interval=10s --timeout=4s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready',timeout=3).close()"
