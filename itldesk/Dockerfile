# UnityDesk Server - imagem compilada a partir do código-fonte deste repositório
# Build:  docker build -f itldesk/Dockerfile -t itldesk-server:latest .

FROM rust:1-bookworm AS build
WORKDIR /src
COPY . .
RUN cargo build --release --bin hbbs --bin hbbr --bin rustdesk-utils \
 && mkdir -p /out \
 && cp target/release/hbbs target/release/hbbr /out/ \
 && cp target/release/rustdesk-utils /out/itldesk-utils

FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/ /usr/bin/
LABEL org.opencontainers.image.title="UnityDesk Server" \
      org.opencontainers.image.licenses="AGPL-3.0"
WORKDIR /data
VOLUME /data
EXPOSE 21115 21116 21116/udp 21117 21118 21119
