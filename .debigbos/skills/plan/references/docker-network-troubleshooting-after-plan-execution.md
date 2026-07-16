# Docker Network Troubleshooting After Plan Execution

## Common scenario

A plan modifies Docker networking — moves services between networks, adds/removes network connections, or changes `depends_on` chains. After rebuilding/recreating, containers can't resolve each other.

## Symptom

In Vite proxy logs (docker logs app):

```
[vite] http proxy error: /api/auth/login/
Error: getaddrinfo EAI_AGAIN api
Error: getaddrinfo ENOTFOUND api
```

The browser sees HTTP 500 (the Vite proxy can't forward the request).

## Root cause

Containers are on different Docker networks. The proxy target hostname (e.g. `api`) is only resolvable from within its own network.

Inspect current network membership:

```
docker inspect <container> --format='{{json .NetworkSettings.Networks}}'
```

## Fix

**Option A (immediate, no downtime for other containers):** Connect the container to the missing network:

```
docker network connect <target-network> <container>
```

Example:
```
docker network connect smartservices_frontend api
```

**Option B (rebuild):** Recreate the container with updated docker-compose config:

```
docker-compose up -d --force-recreate <service>
```

This picks up any network changes from the current docker-compose.yaml.

## Prevention

After any plan task that modifies Docker networking, add a verification step:

1. `docker inspect <container>` — confirm network membership
2. `docker exec <source> curl <target>:<port>` — confirm connectivity  
3. Visit the app and check browser console / Vite logs for proxy errors

## Note

`docker-compose restart` does NOT pick up network changes (networks are assigned at container creation). Use `--force-recreate` or `docker network connect` instead.
