# SDP API — ID Field Mapping Fix

**File:** `ss_sdp_api/ss_api.php`  
**Deploy to:** `C:\Program Files (x86)\SDP\htdocs\system\application\controllers\api\`

## Problem

The SDP PHP API's `buildMap()` mapped `id` to `NOMOR_INDUK` (identity/induk number). But `NOMOR_INDUK` is NOT unique per case — one person can have multiple perkara (cases). Using it as the `id` field caused:
- Duplicate WBPs in search results (same person, different cases)
- Wrong key for linking pendaftaran to local WBP records

## Fix

Change `buildMap()` to map `id` from `ID_PERKARA` (primary key of perkara table):

```php
// Sebelum:
if (in_array($lc, array('id','id_wbp',...,'nomor_induk'))) $map['id'] = $c;

// Sesudah:
if (in_array($lc, array('id','id_wbp',...,'id_perkara'))) $map['id'] = $c;
```

Also update `detail()`:
```php
// WHERE clause
$this->db->where('p.ID_PERKARA', $id);  // was: p.NOMOR_INDUK

// Response
'id' => $row['ID_PERKARA'],  // was: NOMOR_INDUK
```

## Impact

- `w.id` from SDP search → now returns `ID_PERKARA` (unique per case)
- Local `WargaBinaan.nomor_induk_sdp` stores this value
- Backend pendaftaran lookup by `nomor_induk_sdp` now correctly matches
- Existing WBPs imported before this fix have `nomor_induk_sdp` = old `NOMOR_INDUK` — need re-import

## Notes

- `NOMOR_INDUK` in SDP's `identitas` table = identity number (like KTP number)
- `NOMOR_INDUK` in SDP's `perkara` table = foreign key to identitas
- `ID_PERKARA` = primary key of perkara table — truly unique per case
- `NMR_REG_GOL` = registration number (formal format like `AIII. 0687/P/2025`)
- `NOMOR_BERKAS` = berkas/file number — NOT the right key (user rejected this)
