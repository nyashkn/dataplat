# OpenObserve on the box (Dokploy)

One [OpenObserve](https://github.com/openobserve/openobserve) per box. The pipelines and agents send it
traces and logs, scrubbed of identifiers (`docs/ARCHITECTURE.md`, Telemetry), and `just alerts` defines
what reaches a person. It stores Parquet in the box's RustFS and keeps its own index of those files, so
RustFS's truncated bucket listings don't affect it.

## Deploy

1. In [Dokploy](https://docs.dokploy.com/docs/core/docker-compose), create a Compose service from this
   repo with the compose path `deploy/openobserve/compose.yaml`.
2. In its Environment tab, paste `.env.example` from this folder and fill it in:
   - `RUSTFS_ENDPOINT`: RustFS's S3 API as the box's containers reach it. That is the RustFS container
     on `dokploy-network` (for example `http://rustfs:9000`) or its tailnet address.
   - `RUSTFS_ACCESS_KEY` / `RUSTFS_SECRET_KEY`: a key that can create and write the bucket. Prefer one
     used only for this bucket.
   - `OPENOBSERVE_USER` / `OPENOBSERVE_PASSWORD`: the root account (an email and a long password). The
     pipelines authenticate with the same pair.
   - `OPENOBSERVE_BIND`: the box's tailnet IP (`100.x.y.z`) to open it from the tailnet. The default,
     `127.0.0.1`, keeps it on the box.
   - `OPENOBSERVE_WEB_URL`: the address you open it at. Alert messages link to it.
3. Deploy. The `bucket` service logs `bucket openobserve created` (or `exists`) and exits, then
   `openobserve` starts.
4. Turn on Volume Backups for `openobserve-data`. It holds the metadata and the file index; without it
   the Parquet in RustFS can't be read back.

## Point the project at it

Add these to the pipelines' environment on the box (Infisical):

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://<OPENOBSERVE_BIND>:5080/api/default
OPENOBSERVE_USER=...
OPENOBSERVE_PASSWORD=...
```

Run a pipeline. Its workflows appear under Traces, in the project's stream.

## Alerts

1. In OpenObserve, add one alert destination (a Slack or Telegram webhook, or email once SMTP is set).
2. `infisical run --env=prod -- just alerts --plan` shows what would be created. The first run against a
   real instance is also the check that it accepts the payloads; a rejected field is named in the error.
3. `infisical run --env=prod -- just alerts --apply --destination <name>`.

## Notes

- Image tags default to OpenObserve `v1.0.4` and `curlimages/curl:8.11.1`. Neither could be checked
  from where this was written. If a pull fails, set `OPENOBSERVE_VERSION` to a current tag.
- Retention is 30 days (`OPENOBSERVE_RETENTION_DAYS`). Usage reporting to OpenObserve Inc. is off.
- It is a single node, with no HA. The Enterprise edition (free under 50 GB a day) adds HA, SSO and
  role-based access.
- Don't give it a public domain. If one is ever needed, put authentication in front of it at Traefik.
