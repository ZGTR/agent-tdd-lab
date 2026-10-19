# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS build
ARG WITH_BEDROCK=false
WORKDIR /build
COPY pyproject.toml uv.lock requirements-dev.txt requirements-bedrock.txt README.md LICENSE ./
COPY src ./src
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python -m pip install --require-hashes -r requirements-dev.txt && \
    python -m build --wheel --no-isolation && \
    if [ "$WITH_BEDROCK" = true ]; then \
      python -m pip download --require-hashes --only-binary=:all: \
        -r requirements-bedrock.txt --dest /build/dist; \
    elif [ "$WITH_BEDROCK" != false ]; then exit 2; fi

FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=build /build/dist /tmp/wheels
COPY --chmod=0444 evals/refund-routing.json /opt/agent-tdd/evals/refund-routing.json
COPY --chmod=0444 evals/providers/*.example.json /opt/agent-tdd/evals/providers/
RUN chmod 0555 /opt/agent-tdd /opt/agent-tdd/evals /opt/agent-tdd/evals/providers && \
    python -m pip install --no-index --no-deps /tmp/wheels/*.whl && \
    python -c "import shutil; shutil.rmtree('/tmp/wheels')"
WORKDIR /data
RUN chown 10001:10001 /data
USER 10001:10001
ENTRYPOINT ["agent-tdd"]
CMD ["run", "workflow", "--telemetry", "--result-mode", "metadata"]
