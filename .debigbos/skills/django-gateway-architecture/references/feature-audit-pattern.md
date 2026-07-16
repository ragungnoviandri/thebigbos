# Django Gateway Feature Audit: Tracing an API Feature Across Apps

When a user asks "gimana project kita" or "cek udah bisa jalan semua blm?", or reports a 500 on a specific endpoint in a Django gateway monorepo, trace the feature **end-to-end** across all layers before concluding anything is missing or broken.

## Order of Investigation

### 1. Business Requirements (from user + docs)
- What should the feature do? Ask the user or check project docs (SS_*.md, README.md)
- Identify the key states, transitions, and actors (pengunjung, petugas loket, admin)

### 2. Models — `models.py`
Read the model(s) for the feature:
- Fields and their nullability constraints
- ForeignKey relationships (nullable? cascading?)
- Status choices (TextChoices)
- Any `blank=True` / `null=True` that could cause 500s downstream

**Check:** Does the data model support the business requirements? Look for missing fields, wrong FK direction, inadequate status choices.

### 3. URLs — `api/urls.py`
List all endpoints for the feature:
- Public vs authenticated routes
- Check if expected endpoints exist
- Note URL parameter types (`<uuid:pk>` vs `<str:pk>` — mismatches cause 404 or 500)

### 4. Views — `api/views.py`
For each endpoint in the flow:
- Read the full view function/class
- Check for null-safety guards (`if obj.fk:` before chaining `.fk.attr`)
- Check `select_related` / `prefetch_related` — are they pulling the right FK chains?
- Check status filters (`status='Disetujui'` etc.) — do they match the business flow?
- Check for lazy imports to other apps (see `cross-app-lazy-imports.md`)

### 5. Serializers — `api/serializers.py`
- `SerializerMethodField` that accesses FK chains — null-safe?
- Read-only fields that might block writes
- `extra_kwargs` for nullable FK fields

### 6. Frontend Services — `src/services/*.js`
- What endpoints does the frontend call? (axios paths)
- Do the paths match the backend URL patterns?
- Are there calls to endpoints that don't exist yet?
- Check for functions like `prosesPendaftaran`, `cariWbp` — these are clues that the frontend was built expecting a backend endpoint

### 7. Frontend Components — `src/pages/**/*.js`
- How does the UI handle the API response?
- What state does it expect in the response?
- Check for error handling (try/catch, error state display)

### 8. Reproduce with `curl`
```bash
# Via Nginx (full chain)
curl -s -w "\nHTTP_CODE: %{http_code}\n" -X POST \
  http://localhost/api/antrian/cek-pendaftaran/ \
  -H "Content-Type: application/json" \
  -d '{"barcode":"PK-XXXX"}'

# Direct to Django (bypass Vite/Nginx)
docker compose exec api curl -s \
  http://localhost:5005/api/antrian/cek-pendaftaran/ \
  -H "Content-Type: application/json" \
  -d '{"barcode":"PK-XXXX"}'
```

**Compare 200 vs 500 for different edge cases:**
- Null FK records
- Records with FK populated
- Various status values
- Expired/sesi-selesai records

### 9. Check Docker Logs (for 500s that don't reproduce via curl)
```bash
docker compose logs api --tail 50 | grep -i "error\|traceback\|500"
```

Some 500s are intermittent (connection pool, race condition) or only happen under specific load. If curl returns 200 but the user sees 500, check:
- The exact URL the browser uses (look at frontend service file — maybe it calls a different path)
- Whether axios prepends a base URL
- Whether a stale browser token causes `GracefulJWTAuthentication` to throw

### 10. Compare Against Requirements
After tracing the full flow, map each business requirement to the specific line(s) in code that implements it. Gaps are either:
- **Missing code** (endpoint doesn't exist, status check wrong)
- **Already implemented** (code exists but user didn't know — show them)
- **Null-safety gap** (chained FK access without guard)
- **Frontend/backend mismatch** (frontend expects different response shape)

## Common Findings in This Codebase

| Finding | Check |
|---------|-------|
| `Antrian` → `Kunjungan` → `Pengunjung` FK chain | Null-safe in serializers? |
| `PendaftaranKunjungan.warga_binaan` nullable | Guard with `if wb:` before `wb.identitas` |
| `register()` creates `Menunggu` instead of `Disetujui` | Business requirement may have changed |
| Frontend calls endpoint that doesn't exist yet | Check both sides |
| `select_related('fk__nested')` with nullable FK | Always SELECT_related + guard |

## Pitfalls

- **select_related on nullable FK is safe (LEFT JOIN)**, but chaining `.fk.nested` when fk is None is NOT. The join works fine — it's the Python attribute access that crashes.
- **Django catches `AttributeError` internally** and returns generic 500 with no traceback in the response. You MUST check server logs to see these.
- **Frontend may have been built before backend was complete** — this codeset has frontend code for features that the backend doesn't fully implement yet (e.g., `prosesPendaftaran` existed in frontend but needed the backend endpoint in the same session).
- **Permutation testing**: a view that works with a linked WBP may crash with a free-text one. Always test both cases.
- **Direct API vs proxied API**: `localhost:5005` (direct) may work while `localhost/api/...` (via Vite) returns 500 due to proxy config issues. Test through both channels.
