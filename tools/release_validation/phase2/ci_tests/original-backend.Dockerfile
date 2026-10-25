FROM clothinkai-trial-python-base:20261018
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY wheelhouse/ /opt/wheelhouse/
COPY requirements-linux.lock /opt/requirements-linux.lock
RUN python -m pip install --no-index --no-deps --require-hashes --find-links=/opt/wheelhouse -r /opt/requirements-linux.lock && python -m pip check
COPY backend/app/ /app/app/
LABEL org.opencontainers.image.revision="fe23edebb0f6ab28729bbb903215ed364ea51884" clothinkai.trial.scope="readonly-20261018"
CMD ["python", "/opt/trial/start_backend.py"]
HEALTHCHECK --interval=10s --timeout=4s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready',timeout=3).close()"
