---
name: django-external-media-proxy
description: Pattern integrasi Django + internal file-service untuk download & serve media dari eksternal API via proxy
---

# Django External Media Proxy + File-Service

Skill ini mencakup pola umum: Django backend pake proxy internal buat download file dari eksternal (SDP, API pihak ketiga), upload ke internal file-service, dan serve ke frontend via endpoint download.

## Konsep Arsitektur

```
Frontend ──→ /sdp-proxy/{path} ──→ Django Proxy ──→ Eksternal (SDP)
                                    (Host header, hidden IP)
                          
Backend ──→ http://localhost:5005/sdp-proxy/{path} ──→ download via proxy
         ──→ upload ke file-service → /files/upload/{dir}
         ──→ simpan privateUrl di DB

Frontend ──→ /files/download?privateUrl=identitas/{id}/foto/{id}.{ext}
```

## Pitfalls & Fixes

### 0. Frontend Foto Upload: Jangan Gunakan Base64 Langsung ke DB

**Masalah:** Komponen React pake `FileUploader.readAsBase64(file)` → simpan base64 data URL ke `foto` field (TextField) di DB. Ini NGGAK konsisten dengan sistem lain yang pake file-service.

**Dampak:**
- Base64 ~33% lebih besar dari binary → boros storage DB
- Pas di-load ulang, `format_foto_frontend()` bikin `publicUrl` jadi `/files/download?privateUrl={base64}` — broken image
- Nggak bisa di-serve lewat file-service kayak foto lainnya

**Pattern yang bener — 3-tier foto handling:**

```
1. Upload    → FileUploader.upload('dir', file) → file-service → privateUrl
2. Simpan    → privateUrl string di DB field (bukan base64)
3. Tampilkan → /files/download?privateUrl={privateUrl}
```

**Frontend (React) — pakai `FileUploader.upload()`, bukan `readAsBase64()`:**

```javascript
// ✅ BENER — pake file-service
import FileUploader from 'components/FormItems/uploaders/UploadService';

const handleUpload = async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    FileUploader.validate(file, {});
    const result = await FileUploader.upload('identitas', file, {});
    // result.privateUrl → "identitas/{uuid}.jpg"
    // result.publicUrl  → "/files/download?privateUrl=identitas/{uuid}.jpg"
    form.setFieldValue('foto', [{
      id: result.id,
      publicUrl: result.publicUrl,
      privateUrl: result.privateUrl,
      name: file.name
    }]);
  } catch (error) {
    Errors.showMessage(error);
  }
};

// ❌ SALAH — pake base64
const handleFotoChange = async (event) => {
  const file = event.target.files[0];
  const dataUrl = await FileUploader.readAsBase64(file, {});
  form.setFieldValue('foto', [{ id: 'base64', publicUrl: dataUrl, name: file.name }]);
  // ← base64 gede disimpen di DB, broken pas direload
};
```

**Backend (Django) — parse & format konsisten:**

```python
# Simpan: parse array → ambil privateUrl
def parse_foto_from_body(foto_value):
    if not foto_value:
        return None
    if isinstance(foto_value, list) and len(foto_value) > 0:
        return foto_value[0].get('privateUrl', '')
    return str(foto_value) if foto_value else None

# Baca: format privateUrl → array untuk frontend
def format_foto_frontend(private_url):
    if not private_url:
        return None
    return [{
        'publicUrl': f'/files/download?privateUrl={private_url}',
        'privateUrl': private_url,
    }]
```

**Konsistensi — semua upload foto dalam 1 project harus pake mekanisme yang sama:**

| Komponen | Upload Method | Storage | Display |
|----------|--------------|---------|---------|
| ✅ Carousel | `FileUploader.upload('carousel', file)` → file-service | `privateUrl` di DB | `/files/download?privateUrl=...` |
| ✅ WBP Ambildata | `save_sdp_photo()` → download via proxy → file-service | `privateUrl` di DB | `/files/download?privateUrl=...` |
| ❌ Foto Identitas (sebelum fix) | `readAsBase64()` → base64 inline | base64 string di DB | broken image |
| ✅ Foto Identitas (sesudah fix) | `FileUploader.upload('identitas', file)` → file-service | `privateUrl` di DB | `/files/download?privateUrl=...` |

### 1. Gunicorn Deadlock — workers=1

**Masalah:** Backend panggil dirinya sendiri via `localhost:5005/sdp-proxy/...`. Kalo cuma 1 worker, dia nunggu dirinya sendiri → timeout → 500.

**Fix:** Set `workers >= 2` atau tambah `threads` di gunicorn config:

```python
workers = 3
threads = 2
```

### 2. File-service dengan <path:subpath> Route

**Masalah:** Route `@app.route('/files/upload/<path:subpath>')` — subpath capture SELURUH path termasuk filename. Kalo POST `/files/upload/identitas/foto/id.jpeg`, jadinya save_dir jadi folder dengan nama file.

**Fix:** Kirim directory di URL, filename sebagai form field terpisah:

```python
# Backend:
requests.post(f'{file_service}/files/upload/{subpath}',
    data={'filename': filename},
    files={'file': (filename, content, content_type)})

# File-service:
save_dir = os.path.join(UPLOAD_DIR, subpath)  # subpath = "identitas/foto/{id}"
os.makedirs(save_dir, exist_ok=True)
filename = request.form.get('filename', file.filename)
safe_name = secure_filename(filename)
save_path = os.path.join(save_dir, safe_name)
```

### 3. Konfigurasi Dual-Mode (Domain + IP)

Kalo eksternal API punya dua akses (kantor domain, VPN IP):

```python
# settings.py
SDP_BASE = os.environ.get('SDP_BASE', default='http://domain/sdp')    # URL base (ganti sesuai lokasi)
SDP_HOST = os.environ.get('SDP_HOST', default='domain')               # Host header (tetap)
```

Host header HARUS tetap domain (bukan IP) — server eksternal pake virtual hosting.

### 4. Frontend Photo URL Resolution

Kalo row.foto isinya privateUrl, frontend perlu helper:

```javascript
function getFotoUrl(foto) {
  if (!foto) return null;
  if (foto.startsWith('identitas/') || foto.startsWith('carousel/')) {
    return `/files/download?privateUrl=${foto}`;
  }
  if (foto.includes('sdp.rutanjakpus.id') || foto.includes('/sdp-proxy/')) {
    const path = foto.replace(/^.*sdp\\.rutanjakpus\\.id\\/sdp\\//, '');
    return `/sdp-proxy/${path}`;
  }
  return foto;
}
```

### 5. Import 'from django.conf import settings'

**WAJIB** di setiap file views.py yang pake `settings.*`. Error NameError sering muncul pas ganti hardcode ke config.

Cek cepat: `grep -rn 'settings\.' /app --include='*.py' | grep -v '.pyc' | grep -v 'from django' | head -20`

### 6. Frontend React: Naming Conflict dengan useState

**Masalah:** Kalo bikin fungsi helper `function fotoUrl(foto)` TAPI di komponen juga ada `const [fotoUrl, setFotoUrl] = useState('')`, state variable bakal nge-shadow fungsi helper-nya. React/Vite bundler-nya ngasih nama `fotoUrl2` ke fungsi → error `fotoUrl2 is not a function`.

**Symptom di console:**
```
WbpListTable.js:199 Uncaught TypeError: fotoUrl2 is not a function
```

**Fix:** Jangan pake nama yang sama. Bedain nama fungsi helper:

```javascript
// ✅ Bener — nama beda
function getFotoUrl(foto) { ... }
const [fotoUrl, setFotoUrl] = useState('');   // state

// ❌ Salah — nama sama
function fotoUrl(foto) { ... }
const [fotoUrl, setFotoUrl] = useState('');   // ← shadowing!
```

### 7. Deteksi URL SDP Dinamis (SDP_HOST)

Kalo pake regex hardcode buat extract path dari URL SDP, ganti jadi fungsi dinamis:

```python
# ❌ Dulu: regex hardcode domain
SDP_FOTO_PATTERN = re.compile(r'^.*sdp\\.rutanjakpus\\.id/sdp/(.*?)(\\?.*)?$')

# ✅ Sekarang: extract pake settings.SDP_HOST
def extract_sdp_path(sdp_foto_url):
    if not sdp_foto_url:
        return None
    from django.conf import settings
    marker = f'{settings.SDP_HOST}/sdp/'
    if marker in sdp_foto_url:
        idx = sdp_foto_url.index(marker) + len(marker)
        path = sdp_foto_url[idx:]
        if '?' in path:
            path = path.split('?')[0]
        return path
    # Fallback: proxy-style path
    if '/sdp-proxy/' in sdp_foto_url:
        return sdp_foto_url.split('/sdp-proxy/')[-1]
    # Fallback: bare path
    if sdp_foto_url.startswith('/') and not sdp_foto_url.startswith('//'):
        return sdp_foto_url.lstrip('/')
    return None
```

### 8. Double-Check Pengecekan URL SDP di Frontend

Frontend helper `getFotoUrl()` harus handle 3 format URL:

```javascript
function getFotoUrl(foto) {
  if (!foto) return null;
  // 1. PrivateUrl dari file-service
  if (foto.startsWith('identitas/') || foto.startsWith('carousel/')) {
    return `/files/download?privateUrl=${foto}`;
  }
  // 2. URL SDP penuh (dari API search)
  if (foto.includes('sdp.rutanjakpus.id') || foto.includes('/sdp-proxy/')) {
    const path = foto.replace(/^.*sdp\\.rutanjakpus\\.id\\/sdp\\//, '');
    return `/sdp-proxy/${path}`;
  }
  // 3. Udah URL lengkap (fallback)\n  return foto;\n}\n```\n\n### 9. Timeout Download

Kalo download lewat proxy (eksternal lambat), set timeout cukup besar:

```python
# Handler:
resp = requests.get(proxy_url, timeout=60, stream=True)
# Proxy view:
resp = requests.get(target_url, headers=headers, timeout=60, stream=True)
```

### 10. Folder Structure Rapi

Gunakan {entity_id} sebagai folder name:

```
/uploads/{entity_type}/{entity_id}/
├── foto/{entity_id}.{ext}
└── carousel/{carousel_id}.{ext}
```

## Reference Files

- [`references/smartservices-foto-upload-mapping.md`](skill_view://django-external-media-proxy/references/smartservices-foto-upload-mapping.md) — Code map for SmartServices foto upload: all files, functions, and data flow across React frontend, Django backend, and file-service. Use this when debugging or extending foto upload features.

## Verification

1. Test proxy: `docker compose exec api python -c "import requests; r=requests.get('http://172.27.225.9/sdp/{path}', headers={'Host':'sdp.rutanjakpus.id'}, timeout=10); print(r.status_code, r.headers.get('Content-Type'))"` — isolates container-level network vs proxy code issue
2. Test file-service: `curl -X POST http://localhost:5006/files/upload/{dir} -F "file=@test.jpg" -F "filename=test.jpg"`
3. Test handler: `python -c "from core.sdp_photo_handler import save_sdp_photo; print(save_sdp_photo(url, 'test-id'))"`
4. Cek workers: `ps aux | grep gunicorn`
5. Cek log: `docker compose logs api --tail 20`
