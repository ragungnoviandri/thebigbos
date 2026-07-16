# Django ORM: Nullable ForeignKey — The 500 You Can't See

## The Bug

When you access a nullable FK's chained attribute **without checking for None first**:

```python
# views.py
pendaftaran = PendaftaranKunjungan.objects.get(kode_pendaftaran=barcode)

wb = pendaftaran.warga_binaan    # Can be None — FK is nullable!
ident = wb.identitas             # 💥 AttributeError: 'NoneType' object has no attribute 'identitas'
```

Same crash with `select_related`:

```python
pendaftaran = PendaftaranKunjungan.objects.select_related(
    'warga_binaan__identitas'
).get(kode_pendaftaran=barcode)

wb = pendaftaran.warga_binaan    # Still None if FK doesn't exist
ident = wb.identitas             # 💥 same crash
```

This produces a **500 Internal Server Error** with no traceback in the response — Django catches the `AttributeError` internally and returns a generic 500.

## Root Cause

`select_related` performs a SQL `LEFT OUTER JOIN`. If the FK row doesn't exist, Django stores `None` in the field. Accessing a chained attribute on `None` raises `AttributeError`, which Django catches and returns as 500.

## The Pattern: Always Guard

```python
# ✅ Safer — check FK exists first
wb = pendaftaran.warga_binaan
if wb:
    ident = wb.identitas
    wbp_response = {
        'id': str(wb.id),
        'nama': ident.nama_lengkap or str(ident),
        'nomor_registrasi': wb.nomor_registrasi,
        'linked': True,
    }
else:
    wbp_response = {
        'id': None,
        'nama': pendaftaran.nama_wbp or '',
        'nomor_registrasi': '',
        'linked': False,
    }
```

## When to Suspect

| Symptom | Likely Cause |
|---------|-------------|
| 500 on a read-only GET/POST that worked before | Null FK on a record that used to have one |
| Works with some records but not others | Random subset — those with the FK populated succeed |
| No error in stack trace shown to user | Django catches the exception; check `docker compose logs` |
| `select_related('a__b__c')` in the view | Chain of 3+ joins — any intermediate FK can be null |

## Checking Server Logs

```bash
docker compose logs api --tail 50 | grep -i error
# Look for: AttributeError, 'NoneType' object has no attribute
```

## SQL Sanity Check

```bash
docker compose exec api python -c "
from pendaftaran_kunjungan.models import PendaftaranKunjungan
null_fk = PendaftaranKunjungan.objects.filter(warga_binaan__isnull=True).count()
total = PendaftaranKunjungan.objects.count()
print(f'Nullable FK is empty: {null_fk}/{total}')
"
```

## General Rule

**Whenever you chain `select_related('fk__nested')`, treat every FK in the chain as potentially null, even if you "know" it shouldn't be.** The database may have:
- Orphaned records from earlier migrations
- Records created before the FK was mandatory
- Soft-delete patterns (FK set null on delete)
- Direct database edits
