FROM python:3.14-slim AS builder

WORKDIR /build

COPY requirements.txt setup.py README.md .
COPY loop.py memory.py mission.py out_of_the_box.py web_search.py .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir build && \
    python -m build --wheel

# ── Runtime ──
FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/* && \
    groupadd -r agent && \
    useradd -r -g agent -d /app -s /bin/bash agent && \
    chown -R agent:agent /app

COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && \
    rm -rf /tmp/*.whl

USER agent

# Default: interactive REPL (override with docker run -it ...)
ENTRYPOINT ["openagent"]
CMD []

