FROM node:22-bookworm-slim AS frontend
WORKDIR /source/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
COPY tools/prepare_frontend_assets.mjs /source/tools/prepare_frontend_assets.mjs
COPY tools/check_performance_budget.mjs tools/frontend_performance_budget.json /source/tools/
RUN npm run build && npm run test:performance

FROM golang:1.24-bookworm AS backend
WORKDIR /source
COPY go.mod go.sum ./
RUN go mod download
COPY internal/ ./internal/
COPY cmd/ ./cmd/
RUN CGO_ENABLED=0 go build -trimpath -buildvcs=false -ldflags="-s -w" -o /toolbox ./cmd/toolbox

FROM backend AS notices
RUN apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*
COPY --from=frontend /source/frontend/node_modules ./frontend/node_modules
COPY frontend/package-lock.json ./frontend/package-lock.json
COPY tools/build_go_release.py ./tools/build_go_release.py
COPY packaging/go/licenses ./packaging/go/licenses
RUN python3 tools/build_go_release.py --licenses-only --output /licenses

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tzdata && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 toolbox && useradd --uid 10001 --gid toolbox --no-create-home toolbox \
    && install -d -m 0700 -o toolbox -g toolbox /data
WORKDIR /app
COPY --from=backend /toolbox ./toolbox
COPY --from=notices /licenses ./licenses
COPY --from=frontend /source/frontend/build ./frontend/build
COPY --from=frontend /source/frontend/src/theme/templates ./themes
COPY VERSION LICENSE ./
COPY backend/core/course_selection/THIRD_PARTY_NOTICE.md ./THIRD_PARTY_NOTICE.md
ENV NEU_JWXT_MODE=remote-multi
ENV TZ=Asia/Shanghai
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8000
ENTRYPOINT ["/app/toolbox"]
CMD ["--container", "--listen", "0.0.0.0:8000", "--data", "/data"]
