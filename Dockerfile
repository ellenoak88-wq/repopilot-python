FROM public.ecr.aws/docker/library/python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

FROM base AS test
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements-dev.txt
COPY pyproject.toml ./
COPY repopilot ./repopilot
COPY public ./public
COPY tests ./tests
RUN python -m pytest -q && touch /tests-passed

FROM base AS runtime
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --create-home app \
    && mkdir /app/data && chown app:app /app/data
COPY --from=test /tests-passed /tests-passed
COPY --chown=app:app repopilot ./repopilot
COPY --chown=app:app public ./public
USER app
ENV HOST=0.0.0.0 PORT=3211 DATA_DIR=/app/data
EXPOSE 3211
HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:3211/api/health', timeout=3)"
CMD ["python", "-m", "repopilot"]
