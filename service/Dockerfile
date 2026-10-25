# syntax=docker/dockerfile:1
FROM golang:1.22-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY *.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/ghreport .

FROM alpine:3.20
# CA certificates for TLS; an unprivileged user with a writable home for the cache.
RUN apk add --no-cache ca-certificates \
    && adduser -D -u 10001 app
WORKDIR /home/app
COPY --from=build /out/ghreport /usr/local/bin/ghreport
USER app
# No secrets or URLs are baked into the image: pass GITHUB_TOKEN and
# GITHUB_API_BASE_URL with `docker run -e ...`.
ENTRYPOINT ["/usr/local/bin/ghreport"]
