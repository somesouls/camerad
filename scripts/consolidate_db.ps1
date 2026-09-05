<#
.SYNOPSIS
  Konsolidasi database SQLite Camerad - arsipkan duplikat stale di sub-folder
  \db\ dan bantu memantapkan path DB agar tidak rusak saat folder/CWD berubah.

.DESCRIPTION
  AMAN & NON-DESTRUKTIF:
    * Default DRY-RUN (hanya menampilkan rencana; TIDAK mengubah apa pun).
    * Tidak pernah MENGHAPUS. Duplikat stale hanya DIPINDAH ke folder arsip.
    * File DB kanonik (live) di ROOT TIDAK disentuh (kecuali -FullBackup menyalin).
  ARAH KONSOLIDASI: semua DB tetap di ROOT data (-DataRoot). Sub-folder \db\
  hanya berisi salinan stale (analytics.db, qa.db) yang TIDAK pernah dibaca
  modul mana pun (modul analytics/qa meng-anchor path ke root repo).

.PARAMETER DataRoot
  Folder data tempat file .db berada.
  Default: C:\Users\USER\chatbot\pipeline_lokal

.PARAMETER Execute
  Jalankan aksi sebenarnya (pindah duplikat stale ke arsip). Tanpa switch ini =
  DRY-RUN (simulasi).

.PARAMETER FullBackup
  Salin SEMUA DB kanonik + -wal/-shm ke folder backup bertanggal sebelum aksi.
  CATATAN: ukuran bisa sangat besar (beberapa GB). Pastikan ruang disk cukup.

.EXAMPLE
  # 1) Lihat rencana dulu (tidak mengubah apa pun)
  powershell -ExecutionPolicy Bypass -File scripts\consolidate_db.ps1

.EXAMPLE
  # 2) Eksekusi arsip duplikat stale
  powershell -ExecutionPolicy Bypass -File scripts\consolidate_db.ps1 -Execute

.EXAMPLE
  # 3) Eksekusi + full backup dulu, folder data khusus
  powershell -ExecutionPolicy Bypass -File scripts\consolidate_db.ps1 -DataRoot 'D:\data\pipeline_lokal' -FullBackup -Execute
#>

[CmdletBinding()]
param(
  [string]$DataRoot = "C:\Users\USER\chatbot\pipeline_lokal",
  [switch]$Execute,
  [switch]$FullBackup
)

$ErrorActionPreference = "Stop"
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$Mode = if ($Execute) { "EKSEKUSI" } else { "DRY-RUN (simulasi)" }

function Info($m){ Write-Host "[i] $m" -ForegroundColor Cyan }
function Ok($m){ Write-Host "[OK] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "[!] $m" -ForegroundColor Yellow }
function Err($m){ Write-Host "[X] $m" -ForegroundColor Red }

Write-Host ""
Write-Host "=== Konsolidasi DB Camerad - mode: $Mode ===" -ForegroundColor White
Info "DataRoot : $DataRoot"
Info "Waktu    : $ts"
Write-Host ""

if (-not (Test-Path -LiteralPath $DataRoot)) {
  Err "DataRoot tidak ditemukan: $DataRoot"
  Err "Jalankan ulang dengan -DataRoot '<folder data Anda>'."
  exit 1
}

# --- Inventaris DB kanonik (root): env var -> nama file ---
$Canonical = [ordered]@{
  "PIPELINE_DB_FILE"            = "analytics.db"
  "PIPELINE_QA_DB_FILE"         = "qa.db"
  "AVAYA_DB_FILE"               = "avaya.db"
  "AWE_VEC_DB_FILE"             = "awe_vec.db"
  "SOSMED_DB_FILE"              = "sosmed.db"
  "SOSMED_VEC_DB_FILE"          = "sosmed_vec.db"
  "PIPELINE_PERATURAN_DB_FILE"  = "peraturan.db"
  "PIPELINE_SOP_DB_FILE"        = "sop.db"
  "PIPELINE_KAMUS_DB_FILE"      = "rag_kamus.db"
  "PIPELINE_GOLDEN_DB_FILE"     = "golden.db"
  "PIPELINE_EVAL_DB_FILE"       = "eval.db"
  "PIPELINE_STORE_DB_FILE"      = "pipeline_store.db"
  "PIPELINE_AGENT_LOG_DB_FILE"  = "agent_log.db"
  "PIPELINE_USERS_DB_FILE"      = "users.db"
  "PIPELINE_REPORTS_DB_FILE"    = "reports.db"
  "VOICEBOT_DB_FILE"            = "voicebot.db"
  "PIPELINE_RAG_DB_FILE"        = "rag.db"
  "PIPELINE_KNOB_DB_FILE"       = "knob.db"
  "PIPELINE_DF_WEBHOOK_DB_FILE" = "df_webhook.db"
}

# DB yang default-nya RELATIF ke CWD (paling rawan; WAJIB set absolut di .env)
$CwdRelative = @(
  "agent_log.db","users.db","reports.db","voicebot.db","rag.db","knob.db","df_webhook.db"
)

# --- Duplikat stale yang akan diarsipkan (sub-folder \db\ di dalam DataRoot) ---
$StaleDupes = @("analytics.db","qa.db")
$DbSub = Join-Path $DataRoot "db"

function Get-SizeStr($path){
  if (Test-Path -LiteralPath $path) {
    $len = (Get-Item -LiteralPath $path).Length
    if ($len -ge 1GB) { return ("{0:N2} GB" -f ($len/1GB)) }
    elseif ($len -ge 1MB) { return ("{0:N2} MB" -f ($len/1MB)) }
    elseif ($len -ge 1KB) { return ("{0:N2} KB" -f ($len/1KB)) }
    else { return "$len B" }
  }
  return "(tidak ada)"
}

# ===== 1) Preflight =====
Write-Host "--- 1) Preflight ---" -ForegroundColor White
$procs = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match "python|uvicorn|gunicorn" }
if ($procs) {
  Warn "Terdeteksi proses yang mungkin memakai DB (python/uvicorn/gunicorn):"
  $procs | Select-Object Id, ProcessName | Format-Table | Out-String | Write-Host
  Warn "HENTIKAN aplikasi & scheduler dulu sebelum -Execute"
  Warn "(matikan PIPELINE_SCHEDULER / AWE_SCHEDULER / AWE_PHONE_SCHEDULER atau tutup aplikasi)."
} else {
  Ok "Tidak ada proses python/uvicorn/gunicorn aktif yang terdeteksi."
}
Write-Host ""

# ===== 2) Inventaris DB di ROOT =====
Write-Host "--- 2) Inventaris DB di ROOT ---" -ForegroundColor White
foreach ($k in $Canonical.Keys) {
  $f = $Canonical[$k]
  $p = Join-Path $DataRoot $f
  $flag = if ($CwdRelative -contains $f) { " [CWD-relatif -> WAJIB env absolut]" } else { "" }
  "{0,-28} {1,-18} {2}{3}" -f $k, $f, (Get-SizeStr $p), $flag | Write-Host
}
Write-Host ""

# ===== 3) Duplikat stale di \db\ =====
Write-Host "--- 3) Duplikat stale di sub-folder \db\ ---" -ForegroundColor White
$toArchive = @()
foreach ($f in $StaleDupes) {
  $rootP = Join-Path $DataRoot $f
  $dupP  = Join-Path $DbSub $f
  if (Test-Path -LiteralPath $dupP) {
    Info ("STALE : {0}  ({1})   vs ROOT ({2})" -f $dupP, (Get-SizeStr $dupP), (Get-SizeStr $rootP))
    $toArchive += $dupP
    foreach ($ext in @("-wal","-shm")) {
      $side = $dupP + $ext
      if (Test-Path -LiteralPath $side) { $toArchive += $side }
    }
  } else {
    Ok ("Tidak ada duplikat: {0}" -f $dupP)
  }
}
Write-Host ""

# ===== 4) FullBackup opsional =====
if ($FullBackup) {
  $backupDir = Join-Path $DataRoot ("_backup_db\" + $ts)
  Write-Host "--- 4) FullBackup semua DB kanonik -> $backupDir ---" -ForegroundColor White
  Warn "Ukuran total bisa sangat besar (beberapa GB). Pastikan ruang disk cukup."
  if ($Execute) {
    New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
    foreach ($k in $Canonical.Keys) {
      foreach ($ext in @("","-wal","-shm")) {
        $src = (Join-Path $DataRoot $Canonical[$k]) + $ext
        if (Test-Path -LiteralPath $src) {
          Copy-Item -LiteralPath $src -Destination $backupDir -Force
          Ok ("backup: {0}" -f (Split-Path $src -Leaf))
        }
      }
    }
  } else {
    Info "(dry-run) akan menyalin semua file DB + -wal/-shm ke folder backup."
  }
  Write-Host ""
}

# ===== 5) Arsipkan duplikat stale =====
Write-Host "--- 5) Arsipkan duplikat stale (PINDAH, bukan hapus) ---" -ForegroundColor White
if ($toArchive.Count -eq 0) {
  Ok "Tidak ada yang perlu diarsipkan."
} else {
  $archiveDir = Join-Path $DataRoot ("_archive_db\" + $ts + "\db")
  if ($Execute) {
    New-Item -ItemType Directory -Force -Path $archiveDir | Out-Null
    foreach ($src in $toArchive) {
      Move-Item -LiteralPath $src -Destination $archiveDir -Force
      Ok ("dipindah -> {0}" -f (Join-Path $archiveDir (Split-Path $src -Leaf)))
    }
    Ok ("Selesai. Arsip di: {0}" -f $archiveDir)
  } else {
    foreach ($src in $toArchive) {
      Info ("(dry-run) akan dipindah: {0} -> {1}" -f $src, $archiveDir)
    }
  }
}
Write-Host ""

# ===== 6) Verifikasi & saran .env =====
Write-Host "--- 6) Verifikasi akhir (ROOT) ---" -ForegroundColor White
foreach ($f in $StaleDupes) {
  $rootP = Join-Path $DataRoot $f
  if (Test-Path -LiteralPath $rootP) { Ok ("kanonik utuh: {0} ({1})" -f $f, (Get-SizeStr $rootP)) }
  else { Warn ("PERHATIAN: kanonik {0} tidak ditemukan di root!" -f $f) }
}
Write-Host ""
Write-Host "Salin blok berikut ke .env (sesuaikan folder), lalu RESTART aplikasi:" -ForegroundColor White
Write-Host ""
foreach ($k in $Canonical.Keys) {
  "{0}={1}" -f $k, (Join-Path $DataRoot $Canonical[$k]) | Write-Host
}
Write-Host ""
if (-not $Execute) {
  Warn "Ini DRY-RUN. Jalankan ulang dengan -Execute untuk benar-benar mengarsipkan."
}
Write-Host ""
Info "Uji per-menu setelah restart (lihat peta DB per halaman di Rencana Teknis, Bagian 11.4)."
