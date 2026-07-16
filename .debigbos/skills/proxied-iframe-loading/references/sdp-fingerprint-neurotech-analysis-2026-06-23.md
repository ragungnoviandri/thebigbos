# SDP Fingerprint System Analysis — Neurotech ActiveX SDK

> Analisis dari source halaman `/biometric/register_kunjungan` di SDP RUTAN Jakarta Pusat.
> Tanggal: 2026-06-23.

## Arsitektur: ActiveX Browser Plugin

SDP tidak pake Web SDK / REST API buat sidikjari — pake **Neurotech ActiveX** (Windows browser plugin):

```
Browser (halaman SDP)
  ↓ window.external.* calls (JavaScript → ActiveX)
Neurotech Biometric SDK (terinstall di Windows)
  ↓ USB
Fingerprint Scanner (URU-5000 atau device Neurotech compatible)
```

Semua `window.external.*` call adalah **ActiveX bridge** — bukan REST API. Artinya:
- **Cuma jalan di Windows** (Internet Explorer / browser yg support ActiveX)
- **Gak jalan di web biasa** tanpa Neurotech SDK terinstall
- **Gak bisa di-capture** dari server-side Python

## Flow Enroll (Pendaftaran Sidikjari)

```
1. User klik "Sidik Jari" tab
2. Klik tombol scan → JavaScript panggil:
   window.external.enrollBegin()           ← Inisialisasi scanner
   window.external.enrollStart("pengunjung") ← Mulai capture 2 jari
3. Scanner capture Jempol + Telunjuk
4. Callback: enroll_on_captured(data)
   → data.SIDIK_JARI.LOKASI_JARI_1 (jempol)
   → data.SIDIK_JARI.LOKASI_JARI_2 (telunjuk)
   → Tampilkan gambar grayscale via:
     window.external.enrollGrayscaleBase64ToUrl('pengunjung', 0, data.SIDIK_JARI.LOKASI_JARI_1)
5. Selesai:
   window.external.enrollFinish()
   → Callback: enroll_on_end_success(template_base64)
     → Simpan ke _enrolled_template_base64[prefix] = template_base64
```

## Flow Matching / Verifikasi (Identifikasi)

```
1. User klik "Identifikasi" button
2. Scanner capture sidikjari
3. Matching:
   a. Load existing templates ke matcher:
      window.external.matcherFingerAddByTemplate(item_id, base64_template)
   b. Jalankan identifikasi:
      window.external.identifyLocal()   ← 1:N matching lokal
      ATAU
      window.external.identifyPAS()     ← PAS (Pass Authentication System)
   c. Hasil match → nampilin data pengunjung
```

## Format Data yang Dikirim ke Server

| Field | Format | Deskripsi |
|-------|--------|-----------|
| `TEMPLATES[0]` | base64 string | Template binary Neurotech — **buat matching** |
| `TEMPLATES[1]` | base64 string | Jari kedua template |
| `FINGER_IMAGES[0]` | base64 string | Gambar grayscale sidikjari — **buat dokumentasi** |
| `FINGER_IMAGES[1]` | base64 string | Gambar jari kedua |
| `PENGUNJUNG_FINGER_IDS[0]` | string | ID jari |

Server-side SDP backend simpan **template** buat verifikasi pas keluar.

## Fungsi ActiveX yang Terdeteksi

Dari `window.external.*` di JavaScript:

**Status & Info:**
- `IsDesktopResponse()` — cek koneksi
- `getMacAddress()`, `getIPAddress()`, `getComputerName()` — info PC klien
- `getFingerSetting()` — konfigurasi scanner

**Enroll (Pendaftaran):**
- `enrollBegin()` — inisialisasi
- `enrollStart("pengunjung")` — mulai capture (parameter = prefix/key)
- `enrollFinish()` — selesai → dapet template
- `enrollEnd()` — cleanup
- `setStatusBarMessage("...")` — update status UI

**Matching (Verifikasi):**
- `matcherFingerAddByTemplate(id, base64_template)` — load template ke matcher
- `matcherFingerRemove(id)` — hapus dari matcher
- `identifyLocal()` — 1:N matching di lokal PC
- `identifyPAS()` — matching via PAS server

**Foto/Dokumen:**
- `enrollFotoBase64ToUrl(key, base64, ...)` — tampilkan gambar dari base64
- `enrollFotoUrlToBase64(url)` — konversi balik
- `enrollFotoIsExists(url)` — cek file exists
- `enrollFotoClear()` — reset

**Lainnya:**
- `scanEktp(parm)` — scan e-KTP (pake hardware terpisah)
- `Print(url, title)` — cetak izin kunjungan

## Template Format

Template yang dihasilkan Neurotech adalah **NRecord** (Neurotech proprietary format based on ANSI-378 / ISO-19794-2). Format ini:
- Proprietary, **tidak bisa dibaca** oleh SDK vendor lain
- Bisa dibaca silang antar produk Neurotech (VeriFinger, MegaMatcher)
- Scanner URU-5000 **support** via Neurotech BDM

## Kompatibilitas Scanner

Neurotech SDK via **Biometric Device Manager (BDM)** support 100+ scanner:

| Scanner | Support | Catatan |
|---------|:-------:|---------|
| URU-4000/4500/5000 (Digital Persona) | ✅ | Native via BDM |
| Suprema BioMini | ✅ | |
| SecuGen | ✅ | |
| ZKTeco | ✅ | |
| Neurotech NeuroMouse | ✅ | |

## Integrasi Alternatif (Tanpa Neurotech SDK)

Untuk capture + kirim gambar (tanpa matching):

```
Scanner Baru (SDK manapun) → RAW IMAGE (JPEG) → SmartServices API
                                                        ↓
                                                  SDP via FINGER_IMAGES[]
```

Ini **tidak bisa matching** karena SDP butuh template Neurotech, bukan image.

## Keterbatasan

- **Wajib Windows** — ActiveX gak jalan di Linux/Mac
- **Wajib Neurotech SDK terinstall** — library native
- **Wajib scanner compatible** — URU-5000/Neurotech
- **Lisensi per client** — lisensi Neurotech per PC yg jalanin SDK
  - 3 lisensi nganggur di RUTAN bisa dipake
  - Cukup 1 lisensi di server kalo pake arsitektur remote agent
