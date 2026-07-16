# Cross-Platform Docker: CRLF → LF

## Problem

Shell scripts (`.sh`), Dockerfiles, and config files edited on Windows get CRLF
(`\r\n`) line endings. When executed inside Linux Docker containers, the `\r`
character causes cryptic errors:

```
exec /app/entrypoint.sh: no such file or directory
./ss_api.sh: 2: \r: not found
Unknown command: 'migrate\r'
Invalid line: FILE_SERVICE_BASE = http://file-service:5006
```

The `\r` is invisible in most editors but breaks the shebang, shell parsing,
and environment variable loading.

## Symptoms Checklist

- [ ] `exec ... : no such file or directory` but file exists and is executable
- [ ] `: not found` with no command name (the `\r` gets parsed as a command)
- [ ] `Unknown command: 'xxx\r'` — trailing `\r` appended to command
- [ ] `Invalid line` in `.env` parsing — spaces around `=` from CRLF conversion
- [ ] Problem appears after `git pull` from Linux or editing on Windows

## Fix

### Immediate (fix existing files)

```bash
# Strip CR from all shell scripts
sed -i 's/\r$//' *.sh **/*.sh

# Strip CR from Dockerfiles and configs
sed -i 's/\r$//' Dockerfile* **/Dockerfile*
sed -i 's/\r$//' *.conf **/*.conf

# Fix .env spacing (django-environ doesn't allow spaces around =)
sed -i 's/ = /=/g' .env
```

### Permanent (prevent recurrence)

Create `.gitattributes` in repo root:

```
# Force LF for files that must run on Linux/Docker
*.sh text eol=lf
*.py text eol=lf
Dockerfile* text eol=lf
*.conf text eol=lf
```

This tells Git to auto-convert CRLF → LF on checkout, regardless of the
client OS. Files edited on Windows get CRLF locally but LF in the repo.

### Production Dockerfiles

For production builds that don't use volume mounts, add to Dockerfile:

```dockerfile
RUN apt-get update && apt-get install -y dos2unix && \
    dos2unix /app/entrypoint.sh /app/ss_api.sh && \
    apt-get remove -y dos2unix && apt-get autoremove -y
```

Or inline with sed:

```dockerfile
RUN sed -i 's/\r$//' /app/entrypoint.sh
```

## Verification

```bash
# Check line endings
file entrypoint.sh
# Should show: "ASCII text" (NOT "with CRLF line terminators")

# Verify no \r characters
xxd entrypoint.sh | head -1
# Should show 0a (LF) at end of shebang, NOT 0d 0a (CRLF)
```

## Related

- `references/smartservices-pitfalls.md` — Docker + gunicorn reload, nginx 502, SDP patterns
