# Requirement Change Investigation

A repeatable pattern for investigating a business flow change request before writing any code. If you're reading this, you've been given a description of what the user wants changed — use this checklist to ground-truth it against the existing codebase before you plan or build.

## The Pattern

```
User describes flow change
        ↓
  1. Read documentation (feature docs, README)
  2. Read code layer by layer (models → views → serializers/forms)
  3. Map the data flow end-to-end
  4. Synthesize "old vs new" understanding
  5. Confirm understanding with user in their language
  6. Present concrete file-by-file plan
  7. Ask: "Delegation or direct?"
```

---

## Step 1: Read Documentation First

Start with the project's feature docs. Don't assume they're accurate — but they give you the **intended** design which is a critical baseline.

**What to look for:**
- Alur (flow) diagrams — usually in ASCII art
- Model tables — fields, types, FK relationships
- API endpoint tables — method, path, function, request/response
- Feature dependencies — which other apps/services it touches
- Status values and their transitions

```python
# Example: after reading SS_ANTRIAN_KUNJUNGAN.md and SS_PENDAFTARAN_KUNJUNGAN.md
# You know: registration → approval → scan kiosk → queue → counter → visit
```

## Step 2: Read Code Layer by Layer

Always read bottom-up: **models → views → serializers → URLs**.

### Models first
Models are the source of truth. Everything else works around them.

```python
# Read every model in the affected apps
read_file("path/to/app/models.py")
```

**What to check:**
- Field names and types (especially FK/OneToOne relationships)
- Status choices — these define the state machine
- Nullable fields — these are optional paths in the flow
- Constraints — unique_together, indexes that enforce business rules

### Views next
Views implement the logic. Read public (AllowAny) endpoints first, then authenticated ones.

```python
read_file("path/to/app/api/views.py")
```

**What to check:**
- Which statuses each endpoint accepts/rejects
- `transaction.atomic()` blocks — they indicate multi-step writes
- Where FK lookups happen — these are connection points between apps
- Validation logic — what checks block the flow

### Serializers/URLs
Serializers show what data moves in/out. URLs show the contract.

## Step 3: Map the Data Flow

Draw the flow in your head (or write it). Include:
- Who does what (user, system, admin, officer)
- Every status transition
- Every FK that gets set (and when)
- What blocks the flow at each step

**Example from this session (antrian kunjungan flow change):**

**Current flow:**
```
Daftar → Menunggu → Admin Setujui/Tolak → Scan QR kiosk → Antrian → Loket → Buat Kunjungan
```

**Requested flow:**
```
Daftar → Auto Disetujui → Scan QR kiosk → Antrian → Loket → scan QR lagi
                                                          ├── WBP linked → auto-approve
                                                          ├── Free text → cari WBP & link
                                                          └── Tolak → alasan di catatan
```

## Step 4: Synthesize in User's Language

Translate your technical understanding back to the user using their own terminology. Use comparison tables or side-by-side diagrams. This catches misunderstandings before any code is written.

```markdown
**Old flow:** blabla
**New flow:** blabla
```

## Step 5: Confirm Understanding

Don't assume — ask. A simple "Gue jabarin balik, bener gak?" catches 90% of misinterpretations.

## Step 6: Present Concrete File-by-File Plan

Be specific. Every file that needs to change, and what changes. Use a table:

| # | File | Change |
|---|------|--------|
| 1 | `app/api/views.py` | `register()` — auto-set status |
| 2 | `other_app/api/views.py` | `scan_online()` — relax validation |
| 3 | `other_app/api/views.py` | New endpoint for counter processing |

## Step 7: Choose Approach

Ask the user whether they want you to:
- **Handle directly** — best for changes in 1-2 files with simple logic
- **Delegate to sub-agents** — best for multi-file changes, separate backend/frontend work
- **Split into tasks** — parallelize independent workstreams

---

## Worked Example: Smart Services Antrian Kunjungan

This was the concrete session that established this pattern.

**Context:** Multi-service Django project (Smart Services) at a Rutan (detention center). Three interconnected Django apps: `pendaftaran_kunjungan` (online registration), `antrian_kunjungan` (kiosk queue), `kunjungan` (visit records).

**User request:** Remove the admin approval step from the queue workflow. Let visitors register → auto-approved → take queue → only validate at the counter.

**Investigation sequence:**
1. Read `SS_ANTRIAN_KUNJUNGAN.md` (queue system docs with ASCII art flow)
2. Read `SS_PENDAFTARAN_KUNJUNGAN.md` (registration docs with state machine)
3. Read `pendaftaran_kunjungan/models.py` — found `status` field with choices: Menunggu, Disetujui, Ditolak, etc.
4. Read `antrian_kunjungan/models.py` — found `Antrian`, `CounterAntrian`, `LayananKiosk`
5. Read `kunjungan/models.py` — found `Pengunjung` + `Kunjungan` models
6. Read `pendaftaran_kunjungan/api/views.py` — `register()` creates with status Menunggu, `admin_detail()` handles setujui/tolak
7. Read `antrian_kunjungan/api/views.py` — `scan_online()` filters by `status='Disetujui'`
8. Mapped flow old→new
9. Confirmed with user
10. Presented file-by-file plan

**Key files to change (identified):**
- `pendaftaran_kunjungan/api/views.py` — register auto-set Disetujui
- `antrian_kunjungan/api/views.py` — scan_online relax validation
- `antrian_kunjungan/api/views.py` — add counter processing endpoint
