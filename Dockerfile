# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS build
WORKDIR /build
COPY pyproject.toml uv.lock requirements-dev.txt README.md LICENSE ./
COPY src ./src
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python -m pip install --require-hashes -r requirements-dev.txt && \
    python -m build --wheel --no-isolation

FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=build /build/dist /tmp/wheels
RUN python -m pip install --no-index --no-deps /tmp/wheels/*.whl && \
    python -c "import shutil; shutil.rmtree('/tmp/wheels')"
WORKDIR /data
RUN chown 10001:10001 /data
USER 10001:10001
ENTRYPOINT ["agent-tdd"]
CMD ["run", "workflow", "--telemetry", "--result-mode", "metadata"]
