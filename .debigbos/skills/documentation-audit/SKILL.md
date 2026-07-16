---
name: documentation-audit
description: "Cross-reference project documentation against actual code, config, and runtime state to find discrepancies, stale claims, and inaccuracies."
version: 1.1.0
author: BigBos
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [documentation, audit, code-review, quality, verification]
    related_skills: [codebase-inspection, github-code-review]
prerequisites: {}
---

# Documentation Audit — Verify Docs Against Source of Truth

A systematic approach to checking whether a project's documentation still matches its actual code, config files, and runtime behaviour. Catches stale service names, wrong ports, missing features, and outdated metadata before they confuse new developers or cause integration bugs.

## When to Use

- User asks "ada perbedaan ga antara docs dan code?"
- User says "cek dulu setingan X" or "cross-check dokumen"
- User wants you to understand a project by reading its docs
- Before making significant changes to a documented system
- Onboarding to a new project with existing documentation
- After major refactors, renames, or restructures

## Workflow

### Phase 1: Read the Overview

Start with the top-level project documentation:

```
read_file: PROJECT.md or README.md (project root)
```

Extract **claims** that can be verified:
- Port numbers, URLs, hostnames
- Service names, directory names
- Tech stack versions
- Architecture descriptions (diagrams, network topology)
- Quick Start / setup steps
- "Last Updated" timestamps

### Phase 2: Identify Source-of-Truth Files

For each claim, find the authoritative source:

| Claim Type | Source of Truth |
|------------|----------------|
| Port numbers | `docker-compose.yaml`, `nginx` config, server config files |
| Service names / topology | `docker-compose.yaml`, `nginx` config |
| Directory structure | `ls` / `find` the actual repo |
| API endpoints | Django `urls.py`, Flask routes, router config |
| Environment variables | `.env`, `.env.local`, `docker-compose.yaml` env stanzas |
| Tech stack / versions | `Dockerfile`, `requirements.txt`, `package.json`, `pyproject.toml` |

Read the key source-of-truth files:

```
read_file: docker-compose.yaml
read_file: nginx/conf.d/*.conf
read_file: package.json  (for frontend)
```

### Phase 3: Cross-Reference Each Claim

For each claim from documentation, check against the source of truth:

1. **Ports** — does the port in the doc match every config file?
   - e.g. docs say port 8080, but nginx and docker-compose say 5005
2. **Service names** — does the service name exist in docker-compose?
   - e.g. docs mention "demo" service that was removed
3. **Nginx locations** — does the doc show all current proxy locations?
   - Check nginx config for every `location` block
4. **Network topology** — does the diagram match actual docker-compose networks?
5. **Features** — does the doc claim a feature that has changed?
   - e.g. env var defaults vs actual `.env` values
6. **Timestamps** — every "Last Updated" date should be >= the most recent session entry

### Phase 4: Compile Discrepancies

Group findings by severity:

- **Critical** — breaks local development or deployment (wrong port, missing env var)
- **Misleading** — wrong service name, wrong path, stale diagram
- **Cosmetic** — outdated "Last Updated", formatting issues

For each finding, report:
- File + line number
- What it says (current text)
- What it should say (correct text)
- Why (what source of truth proves it)

### Phase 5: Fix (with user approval)

Ask before applying changes. Two approaches:
1. Fix documentation files directly (patch PROJECT.md, etc.)
2. Fix code defaults if wrong (e.g. vite.config.js fallback port)

### Phase 6: Post-Change Documentation Update (Inverse Audit)

After making code changes (fixing bugs, changing flows, adding response fields), run a **reverse audit**: the docs were correct *before*, so which sections need updating now?

**Trigger:** Any code change that affects:
- API endpoint behaviour (new fields, changed statuses, renamed params)
- Business flow / approval logic (status transitions, who approves what)
- UI layout or user workflow (left/right panels, new buttons, new documents printed)
- Architecture claims (port numbers, service names, network topology)

**Workflow:**

1. **List changed files** — `docker compose exec` to read changed Python/JS files, or `git diff --name-only`
2. **For each change, ask:** which doc files reference this feature? Use `search_files` across the docs directory.
3. **Update the doc in lockstep** — don't finish coding without updating the corresponding doc section
4. **Cross-doc consistency** — one API change may affect multiple `.md` files (pendaftaran doc AND antrian doc). Search for related terms across ALL project docs.
5. **Update flow diagrams** — ASCII/Excalidraw diagrams are the most likely to get stale. Update the diagram text and the bullet-point summary below it.
6. **Update API endpoint tables** — method, URL, request body, response fields, status codes
7. **Update Prioritas Build / TODO tables** — mark completed items, adjust remaining scope
8. **Update field tables** — if a model field changed (new field, renamed), update the model reference tables
9. **Verify with one final run** — after all docs are updated, re-test the changed endpoint(s) one more time

This is the inverse of Phase 3 (Cross-Reference Each Claim). Phase 3 starts from docs and finds code mismatches; Phase 6 starts from code and updates the matching docs.

**Key insight:** The most common doc staleness pattern is **not** that the docs were written wrong — it's that the code changed after the docs were written. Writing the doc update at the same time as the code change prevents this.

## Command Quick-Reference

```bash
# Read key config files
cat docker-compose.yaml
cat nginx/conf.d/*.conf
cat ss_app/vite.config.js  # or similar

# Check env defaults
env | grep VITE_
cat ss_app/.env.local 2>/dev/null

# Verify service existence in docker-compose
grep -E '^\s+\w+:\s*#' docker-compose.yaml
grep -E '^\s+\w+:' docker-compose.yaml

# Find all "Last Updated" dates
grep -rn "Last Updated" --include="*.md" .
```

## Pitfalls

1. **Don't trust inline config comments** — they may be stale too. Always compare the *actual* config value, not the comment.
2. **Check fallback defaults** — code often has hardcoded fallbacks (e.g. `|| 'http://localhost:8080'` in vite config). These are hidden sources of truth.
3. **.env is secret-protected** — use cat .env.local via terminal instead of read_file, or check .env.example for expected shape.
4. **Docker-compose can hide ports** — if ports aren't mapped to host (`ports:` block missing), the service is still reachable on the internal Docker network. Check `networks:` to understand connectivity.
5. **Diagrams in docs** are the most likely to be stale — they require manual redraws. Give them extra scrutiny.
6. **Last Updated dates are often concentrated in root README but forgotten in sub-docs.** Check ALL doc files.
7. **search_files and shell commands may be unavailable** — on some environments (Windows without ripgrep, WSL-less installs, restricted containers), `search_files`, `grep`, and `cat` fail silently. The `execute_code` + `os.walk` + `read_file` pattern is the reliable fallback (see technique section below).
8. **One-pass keyword gap** — searching for term X (e.g. "carousel") only finds files that *already* mention it. Files that *should* cover X but don't are invisible to keyword search. After keyword search, do a reverse check: list the files that SHOULD cover the topic and verify each one explicitly.

## Technique: Batch Doc Discovery via execute_code (fallback when search_files fails)

When `search_files`, `grep`, or `find` are unavailable, use Python's `os.walk` + `read_file` inside `execute_code`:

```python
import os

# 1. Discover all .md files in project (exclude vendor/backups/.git)
base = "/path/to/project"
exclude_dirs = {".backup", ".git", "node_modules", "vendor", "__pycache__"}
md_files = []
for root, dirs, files in os.walk(base):
    dirs[:] = [d for d in dirs if d not in exclude_dirs]
    for f in files:
        if f.endswith(".md"):
            md_files.append(os.path.join(root, f))

# 2. Search for specific keywords across all docs
keywords = ["webp", "carousel", "upload", "foto", "file-service"]
for fp in sorted(md_files):
    with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
        content = fh.read()
        for kw in keywords:
            if kw in content.lower():
                print(f"{os.path.relpath(fp, base)}: keyword '{kw}' found")
```

**Key pattern**: Combine discovery (step 1) + keyword search (step 2) + reverse check (step 3: which docs *should* cover the topic but don't mention it). This catches both false positives and omissions. Use `for root, dirs, files in os.walk(...)` and filter `dirs[:]` in-place to prune exclusions efficiently.

## Examples

See `references/smartservices-audit.md` for a real-world example of a 5-file, 8-issue documentation audit.
