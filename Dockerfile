FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.11.23 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    espeak-ng fonts-dejavu-core libgl1 libglib2.0-0 util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home partsdesk
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra facetime
COPY deploy/start.sh /usr/local/bin/partsdesk-start
RUN chmod 755 /usr/local/bin/partsdesk-start
ENV PYTHONUNBUFFERED=1 PARTS_DESK_DATA_DIR=/var/data PARTS_DESK_LIVE=0 PORT=8000
EXPOSE 8000
CMD ["/usr/local/bin/partsdesk-start"]
