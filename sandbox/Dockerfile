FROM python:3.13-slim
RUN pip install --no-cache-dir pytest==8.4.2 ruff==0.13.2 \
    && mkdir /repo && chmod 755 /repo
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
USER 65534:65534
WORKDIR /repo
ENTRYPOINT ["python"]
