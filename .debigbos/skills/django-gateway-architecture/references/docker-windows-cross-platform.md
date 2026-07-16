# Docker Windows Cross-Platform Fixes

## CRLF Line Endings in Shell Scripts

When a repo is shared between Windows and Linux developers, shell scripts (`.sh`) often get CRLF (`\r\n`) line endings from Git's `core.autocrlf=true` on Windows. Docker Linux containers can't execute these — the `\r` at the end of each line causes cryptic failures.

### Symptoms

```
./ss_api.sh: 2: \r: not found
Unknown command: 'migrate\r'. Did you mean migrate?
```

### Permanent Fix — `.gitattributes`

Force LF for all text files that Docker containers will execute:

```
# .gitattributes (in repo root)
*.sh text eol=lf
*.py text eol=lf
Dockerfile* text eol=lf
*.conf text eol=lf
```

### Immediate Fix — strip existing CRLF

```bash
sed -i 's/\r$//' *.sh
# Or fix a single file:
sed -i 's/\r$//' ss_api/ss_api.sh
```

### How to detect

```bash
file *.sh
# "ASCII text" = clean (LF)
# "with CRLF line terminators" = broken
```

---

## django-environ `.env` Space-Around-Equals

`django-environ` (used in `core/settings.py` via `env.read_env()`) is **strict** about `.env` syntax. Spaces around `=` are NOT valid:

```
# ❌ BROKEN — "Invalid line" warning
FILE_SERVICE_BASE = http://file-service:5006

# ✅ CORRECT
FILE_SERVICE_BASE=http://file-service:5006
```

### Symptoms

```
Invalid line: FILE_SERVICE_BASE = http://file-service:5006
```

The variable silently becomes unavailable — any code depending on `env('FILE_SERVICE_BASE')` will get the default value or raise an error.

### Fix

```bash
# Fix a specific line
sed -i 's/FILE_SERVICE_BASE = /FILE_SERVICE_BASE=/' .env

# Fix ALL lines with spaces around = (careful with values containing =)
sed -i 's/ = /=/' .env
```
