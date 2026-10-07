# Stage 15 deployment (detailed plan section 13)

The current deployment target is the local Windows machine. Build the frontend
and run `uv run python -m app.scripts.run_api --serve-ui --port 8001` from the
repository root to serve the UI and API together at http://127.0.0.1:8001.
See the [local deployment instructions](../README.md#stage-15-local-deployment)
for setup and restart steps. The Linux hosting guide below remains deferred.

This is a portable deployment foundation for a shared, trusted team's workspace.
It has not been deployed or smoke-tested on a live host. The API/web host needs
Linux Docker Engine with Compose; the separate execution host needs Linux,
Docker Engine, Python 3.13+, uv, and systemd. The API host never receives Docker
access. Credentials and a domain must be supplied by the operator.

## Architecture and access

```text
Browser → HTTPS Caddy (Basic auth) → static React UI
                                → /api/* → FastAPI → GitHub / OpenAI
                                                    → private Linux worker
                                                        → disposable Docker containers
```

Caddy serves the Vite production build and preserves the API's `/api/v1` paths.
It authenticates all site requests with a username and password hash, then
replaces the incoming Authorization header with a server-only API bearer token.
The API verifies that token on every review request and rejects browser
mutations from other origins. No API keys or bearer tokens enter the frontend
build. Reviews are not stored; there is no per-user ownership or tenant
isolation. Use this only for people
authorized to access every repository available to the configured GitHub token.
Use a GitHub credential scoped to that team's repositories.

API ports are not published. API docs are disabled in production.
Internal `/health` reports process liveness; `/ready` reports readiness.
Only Caddy publishes ports 80/443. There is no public readiness endpoint.
Production settings fail startup without a long bearer token or HTTPS origin,
or when local sandbox execution is selected.

The worker receives an exact-SHA archive downloaded by the API. It independently
validates the archive, caps upload size/time and concurrent executions, and runs
the existing fixed pytest/Ruff commands. It holds only its own worker token and
Docker access, with no GitHub or OpenAI credentials. Child containers
receive neither the worker token nor Docker access. The worker's Docker group
membership grants control of its host: dedicate that host to untrusted execution.
Worker outages/busy responses become incomplete evidence, without local fallback.

## Configure and launch the API/web host

Run all commands below from `deploy/` in a Linux checkout.

1. Point the public hostname's DNS A/AAAA records to the API/web host. Allow
   incoming TCP 80/443 and operator SSH. Do not expose the API or Docker.
2. Copy `production.env.example` to `production.env` and set mode 600:

   ```sh
   cp production.env.example production.env
   chmod 600 production.env
   ```

3. Generate independent tokens/passwords using `openssl rand -hex 32` for each.
   Supply the upstream API keys.
   Generate a workspace password hash interactively:

   ```sh
   docker run --rm -it caddy:2-alpine caddy hash-password
   ```

   Put the hash in single quotes in the env file, preserving dollar signs.
   Set `PUBLIC_HOST` to the bare DNS hostname and choose `WORKSPACE_USER`.
   Keep `SANDBOX_ENABLED=false` until the worker is verified.

4. Validate configuration without displaying resolved secrets, then launch:

   ```sh
   docker compose --env-file production.env config --quiet
   docker compose --env-file production.env up -d --build
   docker compose --env-file production.env ps -a
   ```

Caddy starts after API readiness succeeds.

Caddy obtains/renews certificates and redirects HTTP to HTTPS. Preserve its
data volume. Open `https://<PUBLIC_HOST>`, sign in, submit a permitted test PR,
and confirm anonymous requests to both the page and `/api/v1/reviews` return 401.
Confirm review links target the recorded SHA. Do not use `npm run preview` as
the production proxy.

Secrets are runtime environment variables visible to administrators of the
Docker host. Keep env files out of source control, protect host access, and
avoid printing `docker compose config` without `--quiet`. Build contexts exclude
env files, virtual environments, local reports, and credentials. Base images
use version tags; record and pin approved image digests in your release process
after building/scanning them. There is no automatic update or cloud rollout.

## Deploy the Linux sandbox worker

Use a second host joined to the same trusted private network or VPN. Allow its
port 8010 only from the API host, and bind to its private/VPN interface. Plain
HTTP is supported only inside that protected network; use HTTPS termination
if transport crosses an untrusted network. Never expose the worker publicly.
The API container must be able to route to this interface.

1. Install Docker Engine, uv, and Python 3.13+. Create a `review-worker` system
   user/group with Docker group membership. Put this checkout at
   `/opt/code-review-agent`, owned by an administrator and readable by the user.
2. From the checkout root, install locked dependencies and build the trusted image:

   ```sh
   uv sync --frozen --no-dev
   docker build -t code-review-sandbox:stage9 sandbox
   ```

3. Copy `worker.env.example` to `/etc/code-review-worker.env` as root, mode 600.
   Set its private `WORKER_BIND_ADDRESS`, a distinct random
   `SANDBOX_WORKER_TOKEN`, and `SANDBOX_IMAGE` (prefer a trusted digest).
   This env file requires no GitHub/OpenAI credentials.
4. Install and start the unit:

   ```sh
   sudo cp deploy/sandbox-worker.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now sandbox-worker
   sudo systemctl status sandbox-worker
   ```

   Keep exactly one Uvicorn process: its semaphore permits two active requests
   and rejects excess work. Child containers retain stage 9's network, CPU,
   memory, process, timeout, output, and filesystem isolation limits.
5. From a host with private worker access, set `SANDBOX_WORKER_URL` and
   `SANDBOX_WORKER_TOKEN` in the shell without committing them, then run:

   ```sh
   uv run python -m app.scripts.smoke_worker
   ```

   This uses synthetic tests to check non-root execution, blocked networking,
   a read-only repository, absent credentials/socket, and captured failures.
6. Set the same worker URL/token in `production.env`, set `SANDBOX_ENABLED=true`,
   and recreate the API:

   ```sh
   docker compose --env-file production.env up -d api
   ```

The synthetic worker smoke test is required before enabling execution; it has
not been run in this Windows workspace. Worker crashes may leave containers
named `review-sandbox-*`: inspect those on the worker host and remove only
abandoned executions after stopping the worker. Maintain Docker/kernel updates.

## Updates, recovery, and rollback

Record the deployed code revision and image IDs. Before an update, allow
synchronous reviews to finish, then stop web/API. Build the new images and
start the services:

```sh
docker compose --env-file production.env stop web api
docker compose --env-file production.env build
docker compose --env-file production.env up -d
```

To roll back code, deploy the recorded revision/images.

Reviews are not stored, so a review in progress during an API failure/restart
is lost and must be resubmitted. There is no automatic retry, queue, or
cancellation endpoint. Caddy allows 30 minutes for
response headers; a disconnect/timeout does not cancel the server's review.
The API gets a five-minute graceful shutdown window.

For diagnosis, inspect service status, API readiness inside its container,
`docker compose logs --tail 100 api`, and
`journalctl -u sandbox-worker`. Review logs privately; do not publish repository
contents or secrets. Review throughput, rate limits, cost controls, per-user
authorization, monitoring alerts, and live
model/end-to-end deployment measurements remain operational follow-ups.

References: [Compose startup dependencies](https://docs.docker.com/compose/how-tos/startup-order/),
[Caddy authentication](https://caddyserver.com/docs/caddyfile/directives/basic_auth),
[Caddy HTTPS requirements](https://caddyserver.com/docs/automatic-https).
