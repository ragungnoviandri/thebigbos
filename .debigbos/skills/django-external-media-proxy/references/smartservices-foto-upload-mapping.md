# SmartServices Foto Upload — Code Map

> Referensi lokasi kode untuk semua mekanisme foto upload di project SmartServices (Django + React).

## Arsitektur Overview

```
React Frontend (ss_app/)              Django Backend (ss_api/)         File Service
┌──────────────────────┐            ┌───────────────────────┐      ┌──────────────┐
│ IdentitasForm.js     │──POST────→│ settings/views.py     │      │ file-service │
│  - FotoUpload (base64)│          │  - identitas_list()   │      │ port 5006    │
│  - CarouselManager   │          │  - identitas_detail()  │      └──────────────┘
│                      │          │  - carousel_list()     │
│ SdpSearch.js         │──POST────→│ wargabinaan/api/views.py│
│  - handleAmbilData() │          │  - _create_wbp()       │
│                      │          │                        │
│ UploadService.js     │──POST────→│ files_proxy/views.py   │──POST→│ upload(dir)
│  - upload()          │          │  - upload()            │
│  - readAsBase64()    │          │  - download()          │
└──────────────────────┘          └───────────────────────┘
```

## 1. Foto Identitas (Edit Form)

| Layer | File | Fungsi/Component |
|-------|------|------------------|
| React UI | `ss_app/src/pages/identitas/form/IdentitasForm.js` | `FotoUpload` component (handle foto) |
| React UI | `ss_app/src/pages/identitas/form/IdentitasForm.js` | `CarouselManager` component (handle carousel) |
| React UI | `ss_app/src/pages/identitas/form/IdentitasForm.js` | `CarouselThumbnails` component (preview) |
| React UI | `ss_app/src/pages/identitas/form/IdentitasFormPage.js` | Page wrapper, dispatch form actions |
| Fields | `ss_app/src/pages/identitas/identitasFields.js` | `foto: { type: 'images' }` |
| Service | `ss_app/src/services/identitasService.js` | `findIdentitas()`, `updateIdentitas()` |
| Service | `ss_app/src/services/carouselService.js` | `listCarousel()`, `createCarousel()`, `deleteCarousel()` |
| Uploader | `ss_app/src/components/FormItems/uploaders/UploadService.js` | `readAsBase64()` vs `upload()` |
| Backend | `ss_api/settings/views.py` | `identitas_list()`, `identitas_detail()` |
| Model | `ss_api/identitas/models.py` | `Identitas.foto` (TextField), `format_foto_frontend()`, `parse_foto_from_body()` |
| Model | `ss_api/settings/models.py` | `CarouselImage` (file_path, identitas FK) |

## 2. WBP Ambildata (SDP → Lokal)

| Layer | File | Fungsi/Component |
|-------|------|------------------|
| React UI | `ss_app/src/pages/wbp/list/SdpSearch.js` | `handleAmbilData()` — trigger import dari SDP |
| Service | `ss_app/src/services/wbpService.js` | `createWbp(data)` — POST ke `/wargabinaan/` |
| Backend | `ss_api/wargabinaan/api/views.py` | `_create_wbp()` — create/update WBP + download foto |
| Handler | `ss_api/core/sdp_photo_handler.py` | `save_sdp_photo()` — download via proxy → file-service |
| Proxy | `ss_api/sdp_proxy/views.py` | `proxy_full()` — proxy ke SDP external |
| File Proxy | `ss_api/files_proxy/views.py` | `upload()`, `download()`, `delete()` — ke file-service |

## 3. File-Service Pattern

Upload flow (via `UploadService.js`):
```
FileUploader.upload('carousel', file, {})
  → POST /files/upload/carousel  (multipart/form-data)
  → files_proxy/views.py → POST http://file-service:5006/files/upload/carousel
  → file-service saves to /uploads/carousel/{uuid}.{ext}
  → returns { privateUrl: "carousel/{uuid}.{ext}", publicUrl: "/files/download?privateUrl=..." }
```

Download flow:
```
<img src={`/files/download?privateUrl=carousel/{uuid}.{ext}`} />
  → GET /files/download?privateUrl=carousel/{uuid}.{ext}
  → files_proxy/views.py → GET http://file-service:5006/files/download?privateUrl=...
  → file-service returns binary → Django FileResponse
```

## 4. Key Files Quick Reference

```bash
# Frontend components
ss_app/src/pages/identitas/form/IdentitasForm.js
ss_app/src/pages/wbp/list/SdpSearch.js
ss_app/src/services/carouselService.js
ss_app/src/services/wbpService.js
ss_app/src/components/FormItems/uploaders/UploadService.js

# Backend API
ss_api/settings/views.py            # identitas + carousel CRUD
ss_api/wargabinaan/api/views.py     # WBP CRUD + SDP ambildata
ss_api/identitas/models.py          # Identitas model + foto helpers
ss_api/settings/models.py           # CarouselImage model

# File handling
ss_api/core/sdp_photo_handler.py    # SDP photo download → file-service
ss_api/files_proxy/views.py         # file-service proxy
ss_api/sdp_proxy/views.py           # SDP external proxy
```

## 5. Common Debugging

| Problem | Likely Cause | Check |
|---------|-------------|-------|
| Foto nggak muncul setelah upload | Base64 disimpen di DB. Cek `identitas.foto` isinya privateUrl atau data:image | `select foto from identitas_identitas where id = '...';` |
| Carousel upload gagal | File-service unreachable | `curl http://localhost:5006/files/health` |
| AmbilData timeout | Gunicorn workers=1 (deadlock) | `docker compose logs api` |
| SDP proxy 500 | Host header mismatch | Cek `SDP_HOST` di settings |
