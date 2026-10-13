# ZWA exchange engine: order service, restricted demo signer and portal API.
# Build context: the repository root (see deploy/README.md). The pinned
# transaction tool arrives as deploy/build/tx-tool.tar, a `git archive` of
# commit 16052323904be8efbb9e0f92d059fa57c9198f8a made by
# deploy/scripts/prepare-build-context.sh (zcutil/fetch-params.sh excluded).

FROM rust:1.85.0-bookworm AS txtool
ARG TX_TOOL_REVISION=16052323904be8efbb9e0f92d059fa57c9198f8a
ARG TX_TOOL_SHA256
RUN apt-get update && apt-get install -y --no-install-recommends libsqlite3-dev pkg-config clang \
    && rm -rf /var/lib/apt/lists/*
COPY deploy/build/tx-tool.tar /src/tx-tool.tar
RUN test -n "$TX_TOOL_SHA256" && echo "$TX_TOOL_SHA256  /src/tx-tool.tar" | sha256sum -c - \
    && mkdir /src/tx && tar -xf /src/tx-tool.tar -C /src/tx \
    && test ! -e /src/tx/zcutil/fetch-params.sh
WORKDIR /src/tx
RUN cargo build --release --locked \
    && mkdir -p /out/target/release /out/migrations \
    && cp target/release/zcash_tx_tool /out/target/release/ \
    && cp -r migrations/wallet_init /out/migrations/ \
    && echo "$TX_TOOL_REVISION" > /out/.zwa-tx-tool-revision

FROM node:22.21.0-bookworm-slim AS proofjs
WORKDIR /proof
COPY deploy/engine/protocol-js/package.json deploy/engine/protocol-js/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts \
    && echo "e99bbb85ee536fd64476f87492665d1a382e930e4e412278a3fd8e2f987c283f  node_modules/snarkjs/build/main.cjs" | sha256sum -c -

FROM node:22.21.0-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 procps sqlite3 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 zwa && useradd --system --uid 10001 --gid zwa --home-dir /nonexistent zwa \
    && mkdir -p /var/lib/zwa /opt/zwa/params/ZcashParams && chown zwa:zwa /var/lib/zwa /opt/zwa/params/ZcashParams \
    && chmod 0700 /var/lib/zwa
COPY --from=txtool /out /opt/zwa/tx-tool
# Same sibling layout the pinned verifier expects: ../ZWA-Protocol next to the product.
COPY circuits/shared /app/ZWA-Protocol/circuits/shared
COPY --from=proofjs /proof/node_modules /app/ZWA-Protocol/node_modules
COPY checkout /app/ZWA-Brand-Integration/checkout
COPY exchange /app/ZWA-Brand-Integration/exchange
RUN rm -rf /app/ZWA-Brand-Integration/checkout/operator /app/ZWA-Brand-Integration/checkout/postman \
    && find /app -name "test_*.py" -delete && find /app -name "__pycache__" -prune -exec rm -rf {} + \
    && echo "d83e76894d8754ce0e35d3306d7a1bcaa2d3f3d8573615cd74c7026181d08baf  /app/ZWA-Brand-Integration/checkout/authorization/native_zec_v2.js" | sha256sum -c -
WORKDIR /app/ZWA-Brand-Integration
ENV PYTHONPATH=/app/ZWA-Brand-Integration PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
VOLUME ["/var/lib/zwa"]
CMD ["python3", "-m", "exchange.order_service", "--help"]
