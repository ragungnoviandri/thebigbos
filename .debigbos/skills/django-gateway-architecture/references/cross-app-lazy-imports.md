# Cross-App Lazy Imports in Django Gateway Projects

## Problem

In a gateway architecture where one Django app (e.g. `antrian_kunjungan`) needs to reference models from another app (`pendaftaran_kunjungan`) at view call time, importing at module level causes circular imports or `AppRegistryNotReady` errors — especially when both apps have interdependent signals, admin registrations, or URL configurations.

## Pattern: Lazy Import with Module-Level Cache

Instead of a top-level `from pendaftaran_kunjungan.models import PendaftaranKunjungan`, use a lazy helper function with a `global` cache flag:

```python
_pendaftaran_models_loaded = False

def _get_pendaftaran_kunjungan_model():
    global _pendaftaran_models_loaded
    if not _pendaftaran_models_loaded:
        from pendaftaran_kunjungan.models import PendaftaranKunjungan as _PK
        globals()['PendaftaranKunjungan'] = _PK
        _pendaftaran_models_loaded = True
    return globals().get('PendaftaranKunjungan')
```

### How it works

1. **Guard flag** (`_pendaftaran_models_loaded`) ensures the import runs exactly once
2. **`globals()` assignment** caches the imported class so subsequent calls are a dict lookup, not a re-import
3. **`try/except ImportError`** for truly optional apps (see below)

### Usage in a view

```python
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def my_view(request):
    PK = _get_pendaftaran_kunjungan_model()
    if PK is None:
        return Response({'error': 'Modul pendaftaran belum tersedia'},
                        status=503)

    try:
        pendaftaran = PK.objects.get(kode_pendaftaran=request.data.get('barcode'))
    except PK.DoesNotExist:
        return Response({'error': 'Not found'}, status=404)
    # ...
```

## When to Use This Pattern

| Scenario | Use Lazy Import? | Alternative |
|----------|-----------------|-------------|
| View references another app's model | ✅ Yes | Top-level import if no circular dep |
| Signal handler references another app | ✅ Yes | Apps `ready()` with `app.models` import |
| Two apps register each other in admin | ✅ Yes | Lazy in `admin.py` `register()` calls |
| Same-app imports | ❌ No | Top-level is fine |
| Third-party library import | ❌ No | Just install & import at top |

## Optional App Pattern

When the other app may not be installed at all (e.g. feature-flag gated), add a `try/except`:

```python
def _get_pendaftaran_model():
    if not hasattr(_get_pendaftaran_model, '_cached'):
        try:
            from pendaftaran_kunjungan.models import PendaftaranKunjungan
            _get_pendaftaran_model._cached = PendaftaranKunjungan
        except ImportError:
            _get_pendaftaran_model._cached = None
    return _get_pendaftaran_model._cached
```

This avoids the `global` keyword by using a function attribute as cache.

## Real Example: Smart Services (June 2026)

In `ss_api/antrian_kunjungan/api/views.py`:

- `antrian_kunjungan` needed `PendaftaranKunjungan` model for the `loket_proses_pendaftaran` endpoint
- `pendaftaran_kunjungan` is a separate app with its own models, serializers, and admin
- Importing at module top worked initially but would break if app loading order changes
- Solution: lazy import with a local helper function cached via `globals()`

```python
def _get_pendaftaran_model():
    global _local_imports_pendaftaran
    if not _local_imports_pendaftaran:
        try:
            from pendaftaran_kunjungan.models import PendaftaranKunjungan as PK
            globals()['_PK'] = PK
            _local_imports_pendaftaran = True
        except ImportError:
            return None
    return globals().get('_PK')
```

## Pitfalls

- **Don't cache `None` forever**: If the app genuinely isn't installed, the function returns `None` every call but does the guard check. The optional-app version avoids even that guard check overhead by setting the cache to `None`.
- **Type hints still need top-level**: If you use the type in function signatures (e.g. `-> PendaftaranKunjungan`), you still need `from __future__ import annotations` or a `TYPE_CHECKING` guard.
- **Not for model inheritance**: If app A's model inherits from app B's model, lazy won't work — the class must exist at module load for MRO resolution.
