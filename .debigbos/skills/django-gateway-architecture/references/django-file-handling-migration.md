# Django File Handling: Flask → Django Migration Guide

## Context

From the Smart Services project: migrating file upload/download/delete logic from a Flask microservice (`ss_file-service/app.py`) into Django (`ss_api/files_proxy/views.py`).

## Migration Phases

### Phase 1 — Proxy pass-through (done)
Django `files_proxy` forwards requests to Flask file-service verbatim.

### Phase 2 — Direct file handling in Django (planned)
- Copy validation logic from Flask: `allowed_file()`, `secure_filename()`, path traversal check
- Add `FILE_UPLOAD_DIR` setting (separate from `MEDIA_ROOT`)
- Views handle `request.FILES` directly with `file.chunks()`
- Keep same API contract as Flask (same response format, same status codes)

### Phase 3 — Remove Flask

Commit hash: `888b394` (Smart Services project)

Changes:
- Delete `file-service` from docker-compose.yaml
- Remove `FILE_SERVICE_BASE` env var from settings.py (dead config)
- Clean up references in docs (INFRASTRUCTURE.md, PROJECT.md)
- Remove proxy code from `files_proxy/views.py`
- **Update `settings/signals.py`** — was calling `requests.delete(f'{settings.FILE_SERVICE_BASE}/files/delete?privateUrl=...')`. Replaced with import of `delete_file()` utility from `files_proxy.views`

## Key Differences: Flask vs Django File Handling

| Aspect | Flask | Django |
|--------|-------|--------|
| File access | `request.files['file']` via Werkzeug | `request.FILES['file']` (MultiValueDict) |
| File saving | `file.save(path)` | `file.chunks()` — iterate over 64KB chunks |
| Stream download | `send_file(real_path)` | `FileResponse(open(path, 'rb'))` |
| Max size | `app.config['MAX_CONTENT_LENGTH']` | Compare `file.size` manually |
| CORS | Manual OPTIONS handler | `@csrf_exempt` + `CORS_ALLOW_ALL_ORIGINS` |

## API Contract (shared between Flask and Django versions)

### Upload
```
POST /files/upload/<subpath>
  Body: multipart/form-data { file, filename }
  Response 201: { message, path }
  Response 400: { error }
  Response 413: { error } (file too large)
```

### Download
```
GET /files/download?privateUrl=<path>
  Response 200: binary file (content-type from extension)
  Response 400: { error } (missing param)
  Response 404: { error } (file not found)
```

### Delete
```
DELETE /files/delete?privateUrl=<path>
  Response 200: { message }
  Response 400: { error }
  Response 404: { error }
```

## Path Traversal Protection

```python
def _validate_path(real_path):
    uploads_real = os.path.realpath(UPLOAD_DIR)
    return os.path.realpath(real_path).startswith(uploads_real)
```

Both Flask and Django versions use `os.path.realpath()` to resolve symlinks and `..` before comparing.

## Max File Size

- Server-side: 10MB hard limit
- Nginx: `client_max_body_size 200M` (for larger responses, not uploads)
- Django: compare `file.size > MAX_FILE_SIZE` in view (not in middleware, because uploads go through Vite proxy)
