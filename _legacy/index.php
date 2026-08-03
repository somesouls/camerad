<?php
/*
|==============================================================================
| index.php  —  DIALOGFLOW ANALYSIS PIPELINE (Step 1 s/d 10)
|==============================================================================
| Gabungan seluruh workflow n8n menjadi 1 file PHP untuk shared hosting.
|
| CARA PAKAI
| 1. Upload file ini ke shared hosting (mis. public_html/pipeline/index.php).
| 2. (Untuk Step 1 & Step 3) letakkan service account Google di:
|       service-account.json  (satu folder dengan index.php)
|    ATAU tempel Access Token manual di masing-masing modal.
| 3. Pastikan folder bisa ditulis (untuk membuat folder _runs/).
| 4. Buka index.php di browser.
|
| KEBUTUHAN PHP: curl, openssl, zip (ZipArchive), json, mbstring.
| Semua ekstensi ini standar di hampir semua shared hosting.
|
| DESAIN STEP
| - Setiap step berdiri sendiri. Output tiap step disimpan di _runs/<run>/.
| - Kalau error di tengah, step lain yang sudah sukses TIDAK hilang.
| - Step berikutnya bisa "Lanjut" memakai hasil step sebelumnya tanpa
|   upload / isi form ulang.
|==============================================================================
*/

error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE & ~E_WARNING);
@set_time_limit(0);
@ini_set('memory_limit', '1024M');
@ignore_user_abort(true);

$CONFIG = [
    // Project Google Cloud (dipakai Step 1 & Step 3).
    'project_id'           => 'avaya-djp-klipbot-prod',
    // Path service account JSON (opsional bila memakai Access Token manual).
    // Bisa dioverride via env PIPELINE_SA_FILE (mis. arahkan ke file di Drive).
    'service_account_file' => getenv('PIPELINE_SA_FILE') ?: (__DIR__ . '/service-account.json'),
    'google_scope'         => 'https://www.googleapis.com/auth/cloud-platform',
    // API key untuk server FastAPI lokal (Step 4 & 5). Harus SAMA dengan
    // PIPELINE_API_KEY di .env skrip Python. Bisa dioverride via env.
    'qwen_api_key'         => getenv('PIPELINE_API_KEY') ?: 'sam-n8n-secret',
    // Base URL FastAPI saat PHP & FastAPI di mesin sama (Colab all-in-one).
    'local_api_base'       => getenv('PIPELINE_API_BASE') ?: 'http://127.0.0.1:8000',
    // Mode Colab: PAKSA Step 4/5 selalu memanggil local_api_base (localhost)
    // dan ABAIKAN kolom Ngrok URL. Mencegah request nyasar ke URL frontend.
    // Set false hanya bila FastAPI benar-benar remote (server terpisah).
    'force_local_api'      => (getenv('PIPELINE_FORCE_LOCAL') !== '0'),
    // Jumlah baris per-chunk saat Step 8 (Qwen) agar tidak kena timeout gateway.
    'mkta_chunk'           => 12,
    // Folder penyimpanan hasil tiap run.
    // Bisa dioverride via env PIPELINE_RUNS_DIR (mis. folder di Drive) agar
    // hasil tetap tersimpan walau PHP dilayani dari salinan lokal.
    'runs_dir'            => getenv('PIPELINE_RUNS_DIR') ?: (__DIR__ . '/_runs'),
];

/*------------------------------------------------------------------
| Router
*-----------------------------------------------------------------*/
$action = isset($_REQUEST['action']) ? (string)$_REQUEST['action'] : '';

if ($action === '') {
    render_page();
    exit;
}

if ($action === 'download') {
    handle_download($CONFIG);
    exit;
}

// Semua action lain mengembalikan JSON.
header('Content-Type: application/json; charset=utf-8');
try {
    switch ($action) {
        case 'state': json_out(get_state($CONFIG)); break;
        case 'reset': json_out(reset_run($CONFIG)); break;
        case 'step1': json_out(step1_pull_logs($CONFIG)); break;
        case 'step2': json_out(step2_json_to_xlsx($CONFIG)); break;
        case 'step3': json_out(step3_training_intent($CONFIG)); break;
        case 'step4': json_out(step4_analyze($CONFIG)); break;
        case 'step5': json_out(step5_qwen_judge($CONFIG)); break;
        case 'step6load': json_out(step6_load($CONFIG)); break;
        case 'step6': json_out(step6_save($CONFIG)); break;
        case 'step7': json_out(step7_mkta($CONFIG)); break;
        case 'step8load': json_out(step8_load($CONFIG)); break;
        case 'step8': json_out(step8_run($CONFIG)); break;
        case 'step9load': json_out(step9_load($CONFIG)); break;
        case 'step9': json_out(step9_save($CONFIG)); break;
        case 'step10': json_out(step10_build($CONFIG)); break;
        case 'step11': json_out(step11_update($CONFIG)); break;
        case 'step12': json_out(avaya1_upload_json($CONFIG)); break;
        case 'step13': json_out(avaya2_pull_intents($CONFIG)); break;
        case 'step14': json_out(avaya3_analyze($CONFIG)); break;
        case 'step14start': json_out(avaya3_start($CONFIG)); break;
        case 'step14progress': json_out(avaya3_progress($CONFIG)); break;
        case 'step14fetch': json_out(avaya3_fetch($CONFIG)); break;
        case 'step15': json_out(avaya4_dashboard($CONFIG)); break;
        case 'step16': json_out(avaya5_excel($CONFIG)); break;
        case 'avayadiag': json_out(avaya_diag($CONFIG)); break;
        case 'step99unused':
            $n = (int)substr($action, 4);
            throw new Exception("Step {$n} belum diimplementasikan. Kerangka UI & mekanisme lanjut sudah siap, tinggal isi logikanya.");
        default:
            throw new Exception('Action tidak dikenal: ' . $action);
    }
} catch (Throwable $e) {
    http_response_code(200);
    echo json_encode(['ok' => false, 'error' => $e->getMessage()], JSON_UNESCAPED_UNICODE);
}
exit;

/*==================================================================
| HELPER UMUM
*=================================================================*/
function json_out($arr) {
    if (!isset($arr['ok'])) $arr['ok'] = true;
    echo json_encode($arr, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
}

function run_id() {
    $r = isset($_REQUEST['run']) ? (string)$_REQUEST['run'] : '';
    if (!preg_match('/^[A-Za-z0-9_\-]{1,64}$/', $r)) {
        throw new Exception('Run ID tidak valid.');
    }
    return $r;
}

function run_dir($cfg, $create = true) {
    $dir = rtrim($cfg['runs_dir'], '/') . '/' . run_id();
    if ($create && !is_dir($dir)) {
        if (!@mkdir($dir, 0775, true) && !is_dir($dir)) {
            throw new Exception('Gagal membuat folder run. Pastikan folder induk bisa ditulis (chmod 755/775).');
        }
    }
    return $dir;
}

function state_path($cfg) { return run_dir($cfg) . '/state.json'; }

function load_state_raw($cfg) {
    $p = state_path($cfg);
    if (is_file($p)) {
        $d = json_decode(file_get_contents($p), true);
        if (is_array($d)) return $d;
    }
    return ['run' => run_id(), 'created' => date('c'), 'steps' => new ArrayObject()];
}

function load_state($cfg) {
    $s = load_state_raw($cfg);
    if (!isset($s['steps']) || !is_array($s['steps'])) $s['steps'] = [];
    return $s;
}

function save_state($cfg, $state) {
    file_put_contents(state_path($cfg), json_encode($state, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT));
}

function set_step($cfg, $n, $data) {
    $state = load_state($cfg);
    $state['steps'][(string)$n] = $data;
    save_state($cfg, $state);
    return $state;
}

function get_state($cfg) {
    $state = load_state($cfg);
    return [
        'run' => $state['run'] ?? run_id(),
        'ngrok_url' => isset($state['ngrok_url']) ? $state['ngrok_url'] : '',
        'steps' => (object)$state['steps'],
    ];
}

function reset_run($cfg) {
    $dir = run_dir($cfg, false);
    if (is_dir($dir)) {
        foreach (glob($dir . '/*') as $f) { @unlink($f); }
        @rmdir($dir);
    }
    return ['cleared' => true];
}

function mime_for_ext($ext) {
    switch (strtolower($ext)) {
        case 'json': return 'application/json; charset=utf-8';
        case 'xlsx': return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
        case 'zip':  return 'application/zip';
        default:     return 'application/octet-stream';
    }
}

function save_artifact($cfg, $n, $ext, $bytes, $downloadName, $summary) {
    $file = "step{$n}.{$ext}";
    file_put_contents(run_dir($cfg) . '/' . $file, $bytes);
    $data = [
        'status'   => 'done',
        'file'     => $file,
        'name'     => $downloadName,
        'ext'      => $ext,
        'mime'     => mime_for_ext($ext),
        'size'     => strlen($bytes),
        'summary'  => $summary,
        'at'       => date('c'),
    ];
    set_step($cfg, $n, $data);
    return $data;
}

function handle_download($cfg) {
    try {
        // Unduhan khusus Step 10: CSV LM / CSV Pembaruan.
        $part = isset($_GET['part']) ? $_GET['part'] : '';
        if ($part === 'lm' || $part === 'pembaruan') {
            $map = ['lm' => ['step10_lm.csv', 'LM.csv'], 'pembaruan' => ['step10_pembaruan.csv', 'Pembaruan.csv']];
            $path = run_dir($cfg) . '/' . $map[$part][0];
            if (!is_file($path)) { http_response_code(404); echo 'File CSV belum dibuat. Jalankan Step 10.'; return; }
            header('Content-Type: text/csv; charset=utf-8');
            header('Content-Disposition: attachment; filename="' . $map[$part][1] . '"');
            header('Content-Length: ' . filesize($path));
            readfile($path);
            return;
        }
    if ($part === 'zip11') {
        $path = run_dir($cfg) . '/step11_usersays.zip';
        if (!is_file($path)) { http_response_code(404); echo 'ZIP belum dibuat. Jalankan Step 11.'; return; }
        header('Content-Type: application/zip');
        header('Content-Disposition: attachment; filename="usersays_updated.zip"');
        header('Content-Length: ' . filesize($path));
        readfile($path);
        return;
    }
        if ($part === 'avayadash') {
            $path = run_dir($cfg) . '/step15_dashboard.html';
            if (!is_file($path)) { http_response_code(404); echo 'Dashboard belum dibuat. Jalankan Step 15.'; return; }
            header('Content-Type: text/html; charset=utf-8');
            header('Content-Length: ' . filesize($path));
            readfile($path);
            return;
        }
        $n = isset($_GET['step']) ? (int)$_GET['step'] : 0;
        $state = load_state($cfg);
        $step = $state['steps'][(string)$n] ?? null;
        if (!$step || empty($step['file'])) { http_response_code(404); echo 'File tidak ditemukan.'; return; }
        $path = run_dir($cfg) . '/' . $step['file'];
        if (!is_file($path)) { http_response_code(404); echo 'File hilang dari server.'; return; }
        header('Content-Type: ' . ($step['mime'] ?? 'application/octet-stream'));
        header('Content-Disposition: attachment; filename="' . str_replace('"', '', $step['name']) . '"');
        header('Content-Length: ' . filesize($path));
        readfile($path);
    } catch (Throwable $e) {
        http_response_code(400);
        echo 'Gagal mengunduh: ' . $e->getMessage();
    }
}

// Ambil bytes input untuk sebuah step: dari upload ATAU dari artifact step lain.
function resolve_input_bytes($cfg, $uploadField, $allowedExts) {
    if (isset($_FILES[$uploadField]) && is_uploaded_file($_FILES[$uploadField]['tmp_name'])) {
        $name = $_FILES[$uploadField]['name'];
        $ext = strtolower(pathinfo($name, PATHINFO_EXTENSION));
        if ($allowedExts && !in_array($ext, $allowedExts, true)) {
            throw new Exception('Format file harus: ' . implode(', ', $allowedExts) . '. Diterima: ' . $name);
        }
        return [file_get_contents($_FILES[$uploadField]['tmp_name']), $name];
    }
    $from = isset($_POST['from_step']) ? (int)$_POST['from_step'] : 0;
    if ($from > 0) {
        $state = load_state($cfg);
        $src = $state['steps'][(string)$from] ?? null;
        if (!$src || empty($src['file'])) throw new Exception("Hasil Step {$from} belum tersedia.");
        $path = run_dir($cfg) . '/' . $src['file'];
        if (!is_file($path)) throw new Exception("File hasil Step {$from} hilang dari server.");
        if ($allowedExts && !in_array(strtolower($src['ext']), $allowedExts, true)) {
            throw new Exception("Hasil Step {$from} bukan format yang dibutuhkan (" . implode(', ', $allowedExts) . ').');
        }
        return [file_get_contents($path), $src['name']];
    }
    throw new Exception('Tidak ada input. Unggah file atau pilih hasil step sebelumnya.');
}

/*==================================================================
| GOOGLE AUTH (service account JWT -> access token)
*=================================================================*/
function b64url($s) { return rtrim(strtr(base64_encode($s), '+/', '-_'), '='); }

function google_token($cfg) {
    static $cached = null;
    $override = isset($_POST['access_token']) ? trim($_POST['access_token']) : '';
    if ($override !== '') return $override;
    if ($cached) return $cached;

    $file = $cfg['service_account_file'];
    if (!is_file($file)) {
        throw new Exception('service-account.json tidak ditemukan dan Access Token kosong. Tempel Access Token di form, atau letakkan service-account.json di folder yang sama.');
    }
    $sa = json_decode(file_get_contents($file), true);
    if (!isset($sa['client_email'], $sa['private_key'])) {
        throw new Exception('service-account.json tidak valid (client_email / private_key hilang).');
    }
    $now = time();
    $aud = $sa['token_uri'] ?? 'https://oauth2.googleapis.com/token';
    $header = b64url(json_encode(['alg' => 'RS256', 'typ' => 'JWT']));
    $claim  = b64url(json_encode([
        'iss'   => $sa['client_email'],
        'scope' => $cfg['google_scope'],
        'aud'   => $aud,
        'iat'   => $now,
        'exp'   => $now + 3600,
    ]));
    $input = $header . '.' . $claim;
    $sig = '';
    if (!openssl_sign($input, $sig, $sa['private_key'], OPENSSL_ALGO_SHA256)) {
        throw new Exception('Gagal menandatangani JWT. Pastikan ekstensi openssl aktif.');
    }
    $jwt = $input . '.' . b64url($sig);

    $ch = curl_init($aud);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => http_build_query([
            'grant_type' => 'urn:ietf:params:oauth:grant-type:jwt-bearer',
            'assertion'  => $jwt,
        ]),
        CURLOPT_TIMEOUT => 30,
    ]);
    $res = curl_exec($ch);
    $err = curl_error($ch);
    curl_close($ch);
    if ($res === false) throw new Exception('Gagal meminta token Google: ' . $err);
    $j = json_decode($res, true);
    if (empty($j['access_token'])) {
        throw new Exception('Token Google gagal: ' . substr($res, 0, 400));
    }
    $cached = $j['access_token'];
    return $cached;
}

/*==================================================================
| HTTP HELPERS
*=================================================================*/
function http_post_json($url, $body, $token, $timeout = 120) {
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => json_encode($body, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
        CURLOPT_HTTPHEADER => [
            'Authorization: Bearer ' . $token,
            'Content-Type: application/json',
        ],
        CURLOPT_TIMEOUT => $timeout,
    ]);
    $res = curl_exec($ch);
    $status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err = curl_error($ch);
    curl_close($ch);
    if ($res === false) return [0, null, $err];
    return [$status, json_decode($res, true), $res];
}

function http_get_json($url, $query, $token, $timeout = 120) {
    $full = $url . (strpos($url, '?') === false ? '?' : '&') . http_build_query($query);
    $ch = curl_init($full);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HTTPHEADER => ['Authorization: Bearer ' . $token],
        CURLOPT_TIMEOUT => $timeout,
    ]);
    $res = curl_exec($ch);
    $status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err = curl_error($ch);
    curl_close($ch);
    if ($res === false) return [0, null, $err];
    return [$status, json_decode($res, true), $res];
}

/*==================================================================
| XLSX WRITER (tanpa dependensi, pakai ZipArchive)
|  $sheets = [ ['name'=>..,'rows'=>[[..],[..]],'widths'=>[..],'wrapCols'=>[..]] ]
|  Baris pertama dianggap header (bold + freeze).
*=================================================================*/
function xml_esc($s) {
    $s = (string)$s;
    // buang karakter kontrol ilegal untuk XML
    $s = preg_replace('/[\x00-\x08\x0B\x0C\x0E-\x1F]/u', '', $s);
    return htmlspecialchars($s, ENT_QUOTES | ENT_XML1, 'UTF-8');
}

function xlsx_col($n) {
    $s = '';
    while ($n > 0) { $r = ($n - 1) % 26; $s = chr(65 + $r) . $s; $n = intdiv($n - 1, 26); }
    return $s;
}

function xlsx_cell($ref, $val, $style) {
    $sAttr = $style ? ' s="' . $style . '"' : '';
    if (is_int($val) || is_float($val)) {
        return '<c r="' . $ref . '"' . $sAttr . '><v>' . $val . '</v></c>';
    }
    if ($val === '' || $val === null) {
        return '<c r="' . $ref . '"' . $sAttr . '/>';
    }
    return '<c r="' . $ref . '"' . $sAttr . ' t="inlineStr"><is><t xml:space="preserve">' . xml_esc($val) . '</t></is></c>';
}

function xlsx_sheet_xml($sheet) {
    $rows = $sheet['rows'];
    $wrapCols = isset($sheet['wrapCols']) ? $sheet['wrapCols'] : [];
    $widths = isset($sheet['widths']) ? $sheet['widths'] : [];

    $xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';
    $xml .= '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">';
    $xml .= '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>';
    if ($widths) {
        $xml .= '<cols>';
        foreach ($widths as $i => $w) {
            $c = $i + 1;
            $xml .= '<col min="' . $c . '" max="' . $c . '" width="' . $w . '" customWidth="1"/>';
        }
        $xml .= '</cols>';
    }
    $xml .= '<sheetData>';
    foreach ($rows as $ri => $row) {
        $rowNum = $ri + 1;
        $xml .= '<row r="' . $rowNum . '">';
        foreach ($row as $ci => $val) {
            $ref = xlsx_col($ci + 1) . $rowNum;
            if ($rowNum === 1) {
                $style = 1; // bold header
            } elseif (in_array($ci, $wrapCols, true)) {
                $style = 2; // wrap + top
            } else {
                $style = 0;
            }
            $xml .= xlsx_cell($ref, $val, $style);
        }
        $xml .= '</row>';
    }
    $xml .= '</sheetData></worksheet>';
    return $xml;
}

function xlsx_build($sheets) {
    $tmp = tempnam(sys_get_temp_dir(), 'xlsx');
    $zip = new ZipArchive();
    if ($zip->open($tmp, ZipArchive::OVERWRITE) !== true) {
        throw new Exception('Gagal membuat XLSX (ZipArchive). Pastikan ekstensi zip aktif.');
    }

    $zip->addFromString('[Content_Types].xml',
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' .
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' .
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' .
        '<Default Extension="xml" ContentType="application/xml"/>' .
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' .
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' .
        implode('', array_map(function ($i) {
            return '<Override PartName="/xl/worksheets/sheet' . ($i + 1) . '.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>';
        }, array_keys($sheets))) .
        '</Types>');

    $zip->addFromString('_rels/.rels',
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' .
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' .
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>' .
        '</Relationships>');

    // styles: 0 normal, 1 bold header, 2 wrap+top
    $zip->addFromString('xl/styles.xml',
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' .
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' .
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>' .
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>' .
        '<borders count="1"><border/></borders>' .
        '<cellStyleXfs count="1"><xf/></cellStyleXfs>' .
        '<cellXfs count="3">' .
        '<xf xfId="0"/>' .
        '<xf xfId="0" fontId="1" applyFont="1"/>' .
        '<xf xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>' .
        '</cellXfs>' .
        '</styleSheet>');

    $wbSheets = '';
    $wbRels = '';
    foreach ($sheets as $i => $sheet) {
        $sid = $i + 1;
        $name = htmlspecialchars(mb_substr($sheet['name'], 0, 31), ENT_QUOTES);
        $wbSheets .= '<sheet name="' . $name . '" sheetId="' . $sid . '" r:id="rId' . $sid . '"/>';
        $wbRels .= '<Relationship Id="rId' . $sid . '" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet' . $sid . '.xml"/>';
        $zip->addFromString('xl/worksheets/sheet' . $sid . '.xml', xlsx_sheet_xml($sheet));
    }

    $stylesRelId = count($sheets) + 1;
    $zip->addFromString('xl/workbook.xml',
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' .
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' .
        '<sheets>' . $wbSheets . '</sheets></workbook>');

    $zip->addFromString('xl/_rels/workbook.xml.rels',
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' .
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' .
        $wbRels .
        '<Relationship Id="rId' . $stylesRelId . '" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>' .
        '</Relationships>');

    $zip->close();
    $bytes = file_get_contents($tmp);
    @unlink($tmp);
    return $bytes;
}

function zip_build($files) {
    $tmp = tempnam(sys_get_temp_dir(), 'zip');
    $zip = new ZipArchive();
    if ($zip->open($tmp, ZipArchive::OVERWRITE) !== true) {
        throw new Exception('Gagal membuat ZIP.');
    }
    foreach ($files as $f) { $zip->addFromString($f['name'], $f['data']); }
    $zip->close();
    $bytes = file_get_contents($tmp);
    @unlink($tmp);
    return $bytes;
}

/*==================================================================
| STEP 1 — Tarik Log Dialogflow (Google Logging) -> JSON
*=================================================================*/
function step1_pull_logs($cfg) {
    $start = isset($_POST['start_date']) ? trim($_POST['start_date']) : '';
    $end   = isset($_POST['end_date']) ? trim($_POST['end_date']) : '';
    $lang  = strtolower(trim(isset($_POST['bahasa']) ? $_POST['bahasa'] : 'id'));

    if (!in_array($lang, ['id', 'en'], true)) throw new Exception('Bahasa harus id atau en.');
    if (!$start) throw new Exception('Start Date wajib diisi.');
    if (!$end) $end = $start;
    $start = substr($start, 0, 10);
    $end = substr($end, 0, 10);
    if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $start)) throw new Exception('Start Date harus YYYY-MM-DD.');
    if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $end)) throw new Exception('End Date harus YYYY-MM-DD.');

    $today = (new DateTime('now', new DateTimeZone('Asia/Jakarta')))->format('Y-m-d');
    if ($start > $end) throw new Exception("Start Date ($start) tidak boleh lebih besar dari End Date ($end).");
    if ($start >= $today || $end >= $today) throw new Exception("Pilih tanggal sebelum hari ini. Hari ini: $today.");

    $dayMs = 86400000;
    $startMs = strtotime($start . ' 00:00:00 +0700') * 1000;
    $endExclusiveMs = strtotime($end . ' 00:00:00 +0700') * 1000 + $dayMs;
    $rangeDays = intval(($endExclusiveMs - $startMs) / $dayMs);
    if ($rangeDays > 31) throw new Exception("Range terlalu besar: $rangeDays hari. Maksimal 31 hari.");

    $token = google_token($cfg);
    $url = 'https://logging.googleapis.com/v2/entries:list';
    $pageSize = 5000;
    $maxRetries = 5;
    $maxPagesPerSegment = 10000;

    $chunks = [];
    $totalEntries = 0;
    $totalPages = 0;
    $errorCount = 0;
    $errors = [];

    $segStart = $startMs;
    while ($segStart < $endExclusiveMs) {
        $segEnd = min($segStart + $dayMs, $endExclusiveMs);
        $segStartIso = gmdate('Y-m-d\\TH:i:s', intval($segStart / 1000)) . '.000Z';
        $segEndIso = gmdate('Y-m-d\\TH:i:s', intval($segEnd / 1000)) . '.000Z';

        $filter = implode(' AND ', [
            'textPayload:"Dialogflow Response"',
            'textPayload:"lang: \\"' . $lang . '\\""',
            'timestamp >= "' . $segStartIso . '"',
            'timestamp < "' . $segEndIso . '"',
        ]);

        $pageToken = '';
        $pagesInSeg = 0;
        while (true) {
            $body = [
                'resourceNames' => ['projects/' . $cfg['project_id']],
                'filter' => $filter,
                'orderBy' => 'timestamp asc',
                'pageSize' => $pageSize,
            ];
            if ($pageToken !== '') $body['pageToken'] = $pageToken;

            $attempt = 0;
            $resp = null;
            $ok = false;
            while (true) {
                list($status, $json, $raw) = http_post_json($url, $body, $token, 120);
                if ($status >= 200 && $status < 300 && is_array($json) && !isset($json['error'])) {
                    $resp = $json; $ok = true; break;
                }
                $attempt++;
                if ($attempt > $maxRetries) {
                    $errorCount++;
                    $errors[] = "$segStartIso - $segEndIso: HTTP gagal setelah $maxRetries retry: " . substr((string)$raw, 0, 300);
                    break;
                }
                sleep(1);
            }
            if (!$ok) break; // pindah segmen

            $entries = isset($resp['entries']) && is_array($resp['entries']) ? $resp['entries'] : [];
            foreach ($entries as $entry) {
                $chunks[] = json_encode($entry, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
            }
            $totalEntries += count($entries);
            $totalPages++;
            $pagesInSeg++;

            if ($pagesInSeg > $maxPagesPerSegment) {
                $errorCount++;
                $errors[] = "$segStartIso: melebihi $maxPagesPerSegment page, segmen dihentikan.";
                break;
            }
            if (!empty($resp['nextPageToken'])) {
                $pageToken = $resp['nextPageToken'];
            } else {
                break;
            }
        }
        $segStart = $segEnd;
    }

    $content = count($chunks) > 0 ? "[\n" . implode(",\n", $chunks) . "\n]\n" : "[]\n";
    $fileName = "Dialogflow_Log_{$lang}_{$start}_to_{$end}.json";
    $summary = [
        'status' => $errorCount > 0 ? 'Selesai dengan catatan error' : 'Selesai',
        'total_entries' => $totalEntries,
        'total_pages' => $totalPages,
        'error_count' => $errorCount,
        'errors' => array_slice($errors, 0, 20),
    ];
    $data = save_artifact($cfg, 1, 'json', $content, $fileName, $summary);
    return ['step' => 1, 'artifact' => $data];
}

/*==================================================================
| STEP 2 — Convert JSON Log -> XLSX Multi Sheet
*=================================================================*/
function s2_match($text, $regex) {
    if (preg_match($regex, (string)$text, $m)) return isset($m[1]) ? $m[1] : '';
    return '';
}

function step2_json_to_xlsx($cfg) {
    list($bytes, $origName) = resolve_input_bytes($cfg, 'json_file', ['json']);
    $decoded = preg_replace('/^\xEF\xBB\xBF/', '', $bytes);
    $decoded = trim($decoded);
    if ($decoded === '') throw new Exception('File JSON kosong.');

    $items = [];
    $parsed = json_decode($decoded, true);
    if (is_array($parsed)) {
        $items = (array_keys($parsed) === range(0, count($parsed) - 1)) ? $parsed : [$parsed];
    } else {
        // coba JSON Lines
        foreach (preg_split('/\r?\n/', $decoded) as $line) {
            $line = trim($line);
            if ($line === '') continue;
            $v = json_decode($line, true);
            if (is_array($v)) {
                if (array_keys($v) === range(0, count($v) - 1)) { foreach ($v as $x) $items[] = $x; }
                else $items[] = $v;
            }
        }
        if (!$items) throw new Exception('JSON tidak valid (gagal parse array/object maupun JSON Lines).');
    }
    if (!$items) throw new Exception('Tidak ada objek log di dalam file JSON.');

    $rows = [];
    foreach ($items as $item) {
        if (!is_array($item)) continue;
        $insertId = isset($item['insertId']) ? $item['insertId'] : '';
        $textPayload = isset($item['textPayload']) ? $item['textPayload'] : '';
        $traceId = $userPhrase = $botResponse = $intentName = $lang = $waktu = '';
        $score = '';

        if ($textPayload) {
            $traceId = s2_match($textPayload, '/session_id:\s*"([^"]+)"/');
            if (!$traceId) $traceId = isset($item['trace']) ? $item['trace'] : '';
            $waktu = s2_match($textPayload, '/timestamp:\s*"([^"]+)"/');
            $userPhrase = s2_match($textPayload, '/resolved_query:\s*"([^"]+)"/');
            $botResponse = s2_match($textPayload, '/fulfillment\s*\{\s*speech:\s*"((?:[^"\\\\]|\\\\.)*?)"/s');
            $botResponse = str_replace('\\n', "\n", $botResponse);
            $intentName = s2_match($textPayload, '/metadata\s*\{\s*[^}]+?intent_name:\s*"([^"]+)"/s');
            $lang = s2_match($textPayload, '/lang:\s*"([^"]+)"/');
            $score = s2_match($textPayload, '/score:\s*([0-9.]+)/');
        } else {
            $traceId = isset($item['trace']) ? $item['trace'] : '';
        }

        $rows[] = [
            'ID trace' => $traceId,
            'waktu interaksi' => $waktu,
            'user phrase' => $userPhrase,
            'bot response' => $botResponse,
            'intent name' => $intentName,
            'lang' => $lang,
            'insertId' => $insertId,
            'score' => ($score === '' ? '' : (float)$score),
        ];
    }
    if (!$rows) throw new Exception('Tidak ada baris yang dapat diproses dari file JSON.');

    // Statistik intent (desc by count, tie-break first-seen)
    $counts = [];
    $firstSeen = 0;
    foreach ($rows as $r) {
        $intent = $r['intent name'];
        if (!isset($counts[$intent])) { $counts[$intent] = ['count' => 0, 'seen' => $firstSeen++]; }
        $counts[$intent]['count']++;
    }
    $stats = [];
    foreach ($counts as $intent => $info) { $stats[] = ['intent' => $intent, 'count' => $info['count'], 'seen' => $info['seen']]; }
    usort($stats, function ($a, $b) {
        if ($b['count'] !== $a['count']) return $b['count'] - $a['count'];
        return $a['seen'] - $b['seen'];
    });

    $systemIntents = ['System_System_Welcome Intent' => 1, 'System_System_Hubungi Agent' => 1];
    $fallbackIntents = ['System_System_Fallback Intent' => 1, 'System_System_Fallback Intent 2' => 1];

    $systemRows = $fallbackRows = $oneCharRows = $nonFallbackRows = [];
    foreach ($rows as $r) {
        $intent = $r['intent name'];
        $phrase = trim((string)$r['user phrase']);
        if (isset($systemIntents[$intent])) $systemRows[] = $r;
        elseif (isset($fallbackIntents[$intent])) $fallbackRows[] = $r;
        elseif (mb_strlen($phrase) === 1) $oneCharRows[] = $r;
        else $nonFallbackRows[] = $r;
    }

    $header = ['ID trace', 'waktu interaksi', 'user phrase', 'bot response', 'intent name', 'lang', 'insertId', 'score'];
    $toAoa = function ($src) use ($header) {
        $out = [$header];
        foreach ($src as $r) {
            $out[] = [$r['ID trace'], $r['waktu interaksi'], $r['user phrase'], $r['bot response'], $r['intent name'], $r['lang'], $r['insertId'], $r['score']];
        }
        return $out;
    };

    $statAoa = [['intent name', 'jumlah interaksi']];
    foreach ($stats as $s) { $statAoa[] = [$s['intent'], $s['count']]; }

    $sheets = [
        ['name' => 'Interaksi', 'rows' => $toAoa($rows)],
        ['name' => 'Statistik Intent', 'rows' => $statAoa],
        ['name' => 'System', 'rows' => $toAoa($systemRows)],
        ['name' => 'Fallback', 'rows' => $toAoa($fallbackRows)],
        ['name' => '1 Karakter', 'rows' => $toAoa($oneCharRows)],
        ['name' => 'Non Fallback', 'rows' => $toAoa($nonFallbackRows)],
    ];
    $xlsx = xlsx_build($sheets);

    $base = preg_replace('/\.json$/i', '', $origName);
    if ($base === '') $base = 'output_combined_json_data';
    $outName = $base . '.xlsx';

    $summary = [
        'status' => 'Selesai',
        'source_file' => $origName,
        'total_rows' => count($rows),
        'total_intents' => count($stats),
        'total_system' => count($systemRows),
        'total_fallback' => count($fallbackRows),
        'total_one_character' => count($oneCharRows),
        'total_non_fallback' => count($nonFallbackRows),
    ];
    $data = save_artifact($cfg, 2, 'xlsx', $xlsx, $outName, $summary);
    return ['step' => 2, 'artifact' => $data];
}

/*==================================================================
| STEP 3 — Training Phrase & Intent -> 2 XLSX di dalam ZIP
*=================================================================*/
function build_dialogflow_intent_zip($cfg) {
    $token = google_token($cfg);
    $url = 'https://dialogflow.googleapis.com/v2/projects/' . $cfg['project_id'] . '/agent/intents';

    $intents = [];
    $pageToken = '';
    do {
        $q = ['intentView' => 'INTENT_VIEW_FULL', 'pageSize' => 1000, 'languageCode' => 'id'];
        if ($pageToken !== '') $q['pageToken'] = $pageToken;
        list($status, $json, $raw) = http_get_json($url, $q, $token, 120);
        if ($status < 200 || $status >= 300 || !is_array($json) || isset($json['error'])) {
            throw new Exception('Gagal menarik intents Dialogflow: ' . substr((string)$raw, 0, 300));
        }
        if (!empty($json['intents'])) $intents = array_merge($intents, $json['intents']);
        $pageToken = isset($json['nextPageToken']) ? $json['nextPageToken'] : '';
    } while ($pageToken !== '');

    if (!$intents) throw new Exception('Tidak ada intent yang diterima dari Dialogflow API.');

    $trainingRows = [];
    $intentRows = [];
    $skippedPriority = $skippedChild = $noTraining = $noResponse = 0;

    foreach ($intents as $intent) {
        if ((int)(isset($intent['priority']) ? $intent['priority'] : 0) === -1) { $skippedPriority++; continue; }
        if (trim((string)(isset($intent['parentFollowupIntentName']) ? $intent['parentFollowupIntentName'] : '')) !== '') { $skippedChild++; continue; }
        $displayName = trim((string)(isset($intent['displayName']) ? $intent['displayName'] : ''));
        if ($displayName === '') continue;

        $phrases = [];
        foreach ((isset($intent['trainingPhrases']) ? $intent['trainingPhrases'] : []) as $tp) {
            $txt = '';
            foreach ((isset($tp['parts']) ? $tp['parts'] : []) as $part) { $txt .= isset($part['text']) ? $part['text'] : ''; }
            $txt = trim($txt);
            if ($txt !== '') $phrases[] = $txt;
        }
        if (!$phrases) $noTraining++;
        foreach ($phrases as $p) { $trainingRows[] = ['ID' => $displayName, 'Training Phrase' => $p]; }

        $responses = [];
        foreach ((isset($intent['messages']) ? $intent['messages'] : []) as $msg) {
            if (isset($msg['text']['text']) && is_array($msg['text']['text'])) {
                foreach ($msg['text']['text'] as $t) { $t = trim((string)$t); if ($t !== '') $responses[] = $t; }
            }
            if (isset($msg['speech']) && is_string($msg['speech']) && trim($msg['speech']) !== '') $responses[] = trim($msg['speech']);
        }
        $responses = array_values(array_unique($responses));
        $responseText = implode("\n\n", $responses);
        if ($responseText === '') $noResponse++;
        $intentRows[] = ['ID' => $displayName, 'Isi Intent' => $responseText];
    }

    if (!$trainingRows) throw new Exception('Tidak ada training phrase yang berhasil diekstrak.');
    if (!$intentRows) throw new Exception('Tidak ada intent yang berhasil diekstrak.');

    usort($trainingRows, function ($a, $b) {
        $c = strcmp($a['ID'], $b['ID']);
        return $c !== 0 ? $c : strcmp($a['Training Phrase'], $b['Training Phrase']);
    });
    usort($intentRows, function ($a, $b) { return strcmp($a['ID'], $b['ID']); });

    $trainAoa = [['ID', 'Training Phrase']];
    foreach ($trainingRows as $r) { $trainAoa[] = [$r['ID'], $r['Training Phrase']]; }
    $intentAoa = [['ID', 'Isi Intent']];
    foreach ($intentRows as $r) { $intentAoa[] = [$r['ID'], $r['Isi Intent']]; }

    $trainXlsx = xlsx_build([['name' => 'Sheet1', 'rows' => $trainAoa, 'widths' => [55, 70], 'wrapCols' => [1]]]);
    $intentXlsx = xlsx_build([['name' => 'Sheet1', 'rows' => $intentAoa, 'widths' => [55, 100], 'wrapCols' => [1]]]);

    $zip = zip_build([
        ['name' => 'Analisis Fallback - Training Phrase.xlsx', 'data' => $trainXlsx],
        ['name' => 'Analisis Fallback - Intent.xlsx', 'data' => $intentXlsx],
    ]);

    $summary = [
        'status' => 'Selesai',
        'total_intent_dari_api' => count($intents),
        'total_intent_diekspor' => count($intentRows),
        'total_training_phrase' => count($trainingRows),
        'intent_priority_minus_1_dilewati' => $skippedPriority,
        'intent_anakan_dilewati' => $skippedChild,
        'intent_tanpa_training_phrase' => $noTraining,
        'intent_tanpa_respons_teks' => $noResponse,
    ];
    return [$zip, $summary];
}

function step3_training_intent($cfg) {
    list($zip, $summary) = build_dialogflow_intent_zip($cfg);
    $data = save_artifact($cfg, 3, 'zip', $zip, 'Analisis Fallback - Database Dialogflow.zip', $summary);
    return ['step' => 3, 'artifact' => $data];
}

/*==================================================================
| MODUL AWE AVAYA — alur step meniru index.php
|  Step 12: Upload 1+ JSON percakapan AWE Avaya -> gabung -> artifact JSON
|  Step 13: Tarik intent Dialogflow (PERSIS Step 3) -> artifact ZIP
|  Step 14: Analisis coverage+deflection+cluster+sentimen via server -> ZIP
*=================================================================*/
function avaya2_pull_intents($cfg) {
    list($zip, $summary) = build_dialogflow_intent_zip($cfg);
    $data = save_artifact($cfg, 13, 'zip', $zip, 'Avaya - Database Intent Dialogflow.zip', $summary);
    return ['step' => 13, 'artifact' => $data];
}

function avaya1_upload_json($cfg) {
    if (empty($_FILES['json_files']) || empty($_FILES['json_files']['tmp_name'][0])) {
        throw new Exception('Unggah minimal satu file JSON AWE Avaya.');
    }
    $all = []; $perFile = [];
    $names = $_FILES['json_files']['name'];
    foreach ($_FILES['json_files']['tmp_name'] as $i => $tmp) {
        if (!is_uploaded_file($tmp)) continue;
        $data = json_decode(file_get_contents($tmp), true);
        if ($data === null) throw new Exception('JSON tidak valid: ' . $names[$i]);
        if (isset($data['data']) && is_array($data['data'])) $data = $data['data'];
        if (!is_array($data)) throw new Exception('Struktur JSON tak dikenal: ' . $names[$i]);
        if (array_keys($data) !== range(0, count($data) - 1)) $data = [$data];
        $cnt = 0;
        foreach ($data as $row) { if (is_array($row)) { $all[] = $row; $cnt++; } }
        $perFile[] = $names[$i] . ' (' . $cnt . ')';
    }
    if (!$all) throw new Exception('Tidak ada percakapan pada file JSON.');
    $seen = []; $merged = [];
    foreach ($all as $row) {
        $sid = isset($row['sid']) ? (string)$row['sid'] : '';
        if ($sid !== '') { if (isset($seen[$sid])) continue; $seen[$sid] = true; }
        $merged[] = $row;
    }
    $bytes = json_encode($merged, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    $dates = [];
    foreach ($merged as $r) { if (!empty($r['tanggal'])) $dates[] = substr((string)$r['tanggal'], 0, 10); }
    sort($dates);
    $summary = [
        'status' => 'Selesai',
        'file_diunggah' => count($perFile),
        'rincian_file' => $perFile,
        'total_percakapan_gabungan' => count($merged),
        'duplikat_sid_dibuang' => count($all) - count($merged),
        'rentang_tanggal' => $dates ? ($dates[0] . ' s/d ' . end($dates)) : '-',
    ];
    $data = save_artifact($cfg, 12, 'json', $bytes, 'avaya_gabungan.json', $summary);
    return ['step' => 12, 'artifact' => $data];
}

// STEP 14 — Analisis: kirim JSON+intent ke /api/avaya-result, simpan JSON hasil.
function avaya3_analyze($cfg) {
    $rawBase = isset($_POST['ngrok_url']) ? trim($_POST['ngrok_url']) : '';
    $endpoint = api_endpoint($cfg, $rawBase, '/api/avaya-result');
    if (empty($cfg['force_local_api']) && $rawBase !== '') save_ngrok($cfg, $rawBase);

    $state = load_state($cfg);
    $s12 = isset($state['steps']['12']) ? $state['steps']['12'] : null;
    if (!$s12 || empty($s12['file'])) throw new Exception('Jalankan Step 12 (upload JSON) dulu.');
    $jsonBytes = file_get_contents(run_dir($cfg) . '/' . $s12['file']);

    $mode = isset($_POST['mode']) ? $_POST['mode'] : 'auto';
    $srcLabel = 'Unggah manual';
    if ($mode === 'manual') {
        list($train, ) = read_upload('training_file', ['xlsx'], 'Training Phrase');
        list($content, ) = read_upload('content_file', ['xlsx'], 'Intent');
    } else {
        $srcStep = (isset($state['steps']['13']['file'])) ? 13 : ((isset($state['steps']['3']['file'])) ? 3 : 0);
        if (!$srcStep) throw new Exception('Jalankan Step 13 (tarik intent) dulu, atau pilih Unggah manual.');
        $zipBytes = file_get_contents(run_dir($cfg) . '/' . $state['steps'][(string)$srcStep]['file']);
        list($train, $content) = extract_training_intent($zipBytes);
        $srcLabel = 'Step ' . $srcStep;
    }

    $xlsxMime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
    $files = [
        'files'         => [$jsonBytes, 'avaya_gabungan.json', 'application/json'],
        'file_training' => [$train, 'training.xlsx', $xlsxMime],
        'file_intent'   => [$content, 'intent.xlsx', $xlsxMime],
    ];
    $outHeaders = []; $httpCode = 0;
    $body = curl_multipart_raw($endpoint, $cfg['qwen_api_key'], $files, [], $outHeaders, $httpCode);
    $result = json_decode($body, true);
    if (!is_array($result) || !isset($result['dashboard'])) {
        throw new Exception('Server tidak mengembalikan JSON hasil yang valid. Cuplikan: ' . substr((string)$body, 0, 800));
    }
    $d = $result['dashboard'];
    $meta = isset($d['meta']) ? $d['meta'] : [];
    $cov = isset($d['intent_coverage']) ? $d['intent_coverage'] : [];
    $defl = isset($d['deflection']) ? $d['deflection'] : [];
    $summary = [
        'status' => 'Selesai',
        'build_server' => isset($result['build']) ? $result['build'] : '-',
        'mesin' => isset($meta['engine']) ? $meta['engine'] : '-',
        'total_percakapan' => isset($meta['total_conv']) ? $meta['total_conv'] : '-',
        'rentang_tanggal' => (isset($meta['date_min']) ? $meta['date_min'] : '?') . ' s/d ' . (isset($meta['date_max']) ? $meta['date_max'] : '?'),
        'intent_tercover' => isset($cov['covered']) ? $cov['covered'] : '-',
        'intent_belum_tercover' => isset($cov['uncovered']) ? $cov['uncovered'] : '-',
        'deflection_gap' => isset($defl['gap']) ? $defl['gap'] : '-',
        'sumber_intent' => $srcLabel,
    ];
    $data = save_artifact($cfg, 14, 'json', json_encode($result, JSON_UNESCAPED_UNICODE), 'avaya_result.json', $summary);
    return ['step' => 14, 'artifact' => $data];
}

// STEP 14 (async) — START: kirim file, server proses di latar belakang, balikan job_id.
function avaya3_start($cfg) {
    $rawBase = isset($_POST['ngrok_url']) ? trim($_POST['ngrok_url']) : '';
    $endpoint = api_endpoint($cfg, $rawBase, '/api/avaya-result-start');
    if (empty($cfg['force_local_api']) && $rawBase !== '') save_ngrok($cfg, $rawBase);
    $state = load_state($cfg);
    $s12 = isset($state['steps']['12']) ? $state['steps']['12'] : null;
    if (!$s12 || empty($s12['file'])) throw new Exception('Jalankan Step 12 (upload JSON) dulu.');
    $jsonBytes = file_get_contents(run_dir($cfg) . '/' . $s12['file']);
    $mode = isset($_POST['mode']) ? $_POST['mode'] : 'auto';
    if ($mode === 'manual') {
        list($train, ) = read_upload('training_file', ['xlsx'], 'Training Phrase');
        list($content, ) = read_upload('content_file', ['xlsx'], 'Intent');
    } else {
        $srcStep = (isset($state['steps']['13']['file'])) ? 13 : ((isset($state['steps']['3']['file'])) ? 3 : 0);
        if (!$srcStep) throw new Exception('Jalankan Step 13 (tarik intent) dulu, atau pilih Unggah manual.');
        $zipBytes = file_get_contents(run_dir($cfg) . '/' . $state['steps'][(string)$srcStep]['file']);
        list($train, $content) = extract_training_intent($zipBytes);
    }
    $xlsxMime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
    $files = [
        'files'         => [$jsonBytes, 'avaya_gabungan.json', 'application/json'],
        'file_training' => [$train, 'training.xlsx', $xlsxMime],
        'file_intent'   => [$content, 'intent.xlsx', $xlsxMime],
    ];
    $outHeaders = []; $httpCode = 0;
    $body = curl_multipart_raw($endpoint, $cfg['qwen_api_key'], $files, [], $outHeaders, $httpCode);
    $j = json_decode($body, true);
    if (!is_array($j) || empty($j['job_id'])) throw new Exception('Server tidak memberi job_id. Cuplikan: ' . substr((string)$body, 0, 800));
    return ['job_id' => $j['job_id'], 'build' => isset($j['build']) ? $j['build'] : '-'];
}

// STEP 14 (async) — PROGRESS: pantau tahap + jumlah percakapan yang sudah diproses.
function avaya3_progress($cfg) {
    $job = isset($_GET['job']) ? trim($_GET['job']) : '';
    if ($job === '') throw new Exception('job_id kosong.');
    $rawBase = isset($_GET['ngrok_url']) ? trim($_GET['ngrok_url']) : '';
    $endpoint = api_endpoint($cfg, $rawBase, '/api/avaya-progress') . '?job=' . urlencode($job);
    $body = curl_get_raw($endpoint, $cfg['qwen_api_key']);
    $j = json_decode($body, true);
    if (!is_array($j)) throw new Exception('Progress gagal di-parse. Cuplikan: ' . substr((string)$body, 0, 400));
    return ['progress' => $j];
}

// STEP 14 (async) — FETCH: ambil hasil final & simpan artifact bila sudah selesai.
function avaya3_fetch($cfg) {
    $job = isset($_GET['job']) ? trim($_GET['job']) : '';
    if ($job === '') throw new Exception('job_id kosong.');
    $rawBase = isset($_GET['ngrok_url']) ? trim($_GET['ngrok_url']) : '';
    $endpoint = api_endpoint($cfg, $rawBase, '/api/avaya-result-fetch') . '?job=' . urlencode($job);
    $body = curl_get_raw($endpoint, $cfg['qwen_api_key']);
    $result = json_decode($body, true);
    if (!is_array($result)) throw new Exception('Hasil gagal di-parse. Cuplikan: ' . substr((string)$body, 0, 800));
    if (isset($result['finished']) && $result['finished'] === false && !isset($result['dashboard'])) {
        return ['pending' => true, 'progress' => $result];
    }
    if (!isset($result['dashboard'])) throw new Exception('Hasil tidak berisi dashboard. Cuplikan: ' . substr((string)$body, 0, 800));
    $d = $result['dashboard'];
    $meta = isset($d['meta']) ? $d['meta'] : [];
    $cov = isset($d['intent_coverage']) ? $d['intent_coverage'] : [];
    $defl = isset($d['deflection']) ? $d['deflection'] : [];
    $summary = [
        'status' => 'Selesai',
        'build_server' => isset($result['build']) ? $result['build'] : '-',
        'mesin' => isset($meta['engine']) ? $meta['engine'] : '-',
        'total_percakapan' => isset($meta['total_conv']) ? $meta['total_conv'] : '-',
        'rentang_tanggal' => (isset($meta['date_min']) ? $meta['date_min'] : '?') . ' s/d ' . (isset($meta['date_max']) ? $meta['date_max'] : '?'),
        'intent_tercover' => isset($cov['covered']) ? $cov['covered'] : '-',
        'intent_belum_tercover' => isset($cov['uncovered']) ? $cov['uncovered'] : '-',
        'deflection_gap' => isset($defl['gap']) ? $defl['gap'] : '-',
    ];
    $data = save_artifact($cfg, 14, 'json', json_encode($result, JSON_UNESCAPED_UNICODE), 'avaya_result.json', $summary);
    return ['step' => 14, 'artifact' => $data];
}

// STEP 15 — Dashboard: kirim JSON dashboard ke /api/avaya-render, simpan HTML.
function avaya4_dashboard($cfg) {
    $rawBase = isset($_POST['ngrok_url']) ? trim($_POST['ngrok_url']) : '';
    $endpoint = api_endpoint($cfg, $rawBase, '/api/avaya-render');
    $state = load_state($cfg);
    $s14 = isset($state['steps']['14']) ? $state['steps']['14'] : null;
    if (!$s14 || empty($s14['file'])) throw new Exception('Jalankan Step 14 (analisis) dulu.');
    $result = json_decode(file_get_contents(run_dir($cfg) . '/' . $s14['file']), true);
    if (!isset($result['dashboard'])) throw new Exception('Hasil Step 14 tidak berisi data dashboard.');
    $payload = json_encode(['dashboard' => $result['dashboard']], JSON_UNESCAPED_UNICODE);
    $outHeaders = []; $httpCode = 0;
    $html = curl_json_raw($endpoint, $cfg['qwen_api_key'], $payload, $outHeaders, $httpCode);
    if (stripos((string)$html, '<') === false) throw new Exception('Server tidak mengembalikan HTML. Cuplikan: ' . substr((string)$html, 0, 800));
    file_put_contents(run_dir($cfg) . '/step15_dashboard.html', $html);
    $summary = [
        'status' => 'Selesai',
        'ukuran_html' => strlen($html) . ' bytes',
        'catatan' => 'Klik "Buka Dashboard" untuk melihat.',
    ];
    $data = save_artifact($cfg, 15, 'html', $html, 'dashboard.html', $summary);
    return ['step' => 15, 'artifact' => $data];
}

// STEP 16 — Ekspor Excel: kirim JSON {dashboard,records} ke /api/avaya-excel.
function avaya5_excel($cfg) {
    $rawBase = isset($_POST['ngrok_url']) ? trim($_POST['ngrok_url']) : '';
    $endpoint = api_endpoint($cfg, $rawBase, '/api/avaya-excel');
    $state = load_state($cfg);
    $s14 = isset($state['steps']['14']) ? $state['steps']['14'] : null;
    if (!$s14 || empty($s14['file'])) throw new Exception('Jalankan Step 14 (analisis) dulu.');
    $result = json_decode(file_get_contents(run_dir($cfg) . '/' . $s14['file']), true);
    if (!isset($result['dashboard'])) throw new Exception('Hasil Step 14 tidak berisi data dashboard.');
    $payload = json_encode([
        'dashboard' => $result['dashboard'],
        'records' => isset($result['records']) ? $result['records'] : [],
    ], JSON_UNESCAPED_UNICODE);
    $outHeaders = []; $httpCode = 0;
    $xlsx = curl_json_raw($endpoint, $cfg['qwen_api_key'], $payload, $outHeaders, $httpCode);
    if (substr((string)$xlsx, 0, 2) !== 'PK') throw new Exception('Server tidak mengembalikan file XLSX valid. Cuplikan: ' . substr(trim(preg_replace('/\s+/', ' ', strip_tags((string)$xlsx))), 0, 800));
    $summary = [
        'status' => 'Selesai',
        'ukuran' => strlen($xlsx) . ' bytes',
        'isi_sheet' => 'Ringkasan, Percakapan, Agent, Pelanggan NPWP/Non-NPWP, Kandidat Intent Baru',
    ];
    $data = save_artifact($cfg, 16, 'xlsx', $xlsx, 'hasil_avaya.xlsx', $summary);
    return ['step' => 16, 'artifact' => $data];
}

// Diagnostik server AWE Avaya (build, template, dependensi).
function avaya_diag($cfg) {
    $rawBase = isset($_GET['ngrok_url']) ? trim($_GET['ngrok_url']) : (isset($_POST['ngrok_url']) ? trim($_POST['ngrok_url']) : '');
    $endpoint = api_endpoint($cfg, $rawBase, '/api/avaya-diag');
    $body = curl_get_raw($endpoint, $cfg['qwen_api_key']);
    $j = json_decode($body, true);
    if (!is_array($j)) throw new Exception('Diagnostik gagal di-parse. Cuplikan: ' . substr((string)$body, 0, 800));
    $diag = isset($j['diag']) ? $j['diag'] : $j;
    $diag['endpoint'] = $endpoint;
    return ['diag' => $diag];
}

/*==================================================================
| HELPER SERVER COLAB (ngrok) + STEP 4 & 5
*=================================================================*/
// --- Helper server Colab (ngrok) -----------------------------------------
function normalize_ngrok_base($base) {
    $base = trim((string)$base);
    if ($base === '') throw new Exception('Ngrok Base URL wajib diisi.');
    if (!preg_match('#^https://#i', $base)) throw new Exception('Ngrok Base URL harus diawali https://');
    return rtrim($base, '/');
}

function ngrok_endpoint($base, $suffix) {
    $base = normalize_ngrok_base($base);
    if (substr($base, -strlen($suffix)) === $suffix) return $base;
    return $base . $suffix;
}

// Base URL server FastAPI. Bila force_local_api aktif -> selalu localhost
// (abaikan kolom Ngrok URL). Selain itu: pakai URL user, atau localhost bila kosong.
function resolve_api_base($cfg, $raw) {
    if (!empty($cfg['force_local_api'])) return rtrim($cfg['local_api_base'], '/');
    $raw = trim((string)$raw);
    if ($raw === '') return rtrim($cfg['local_api_base'], '/');
    if (!preg_match('#^https?://#i', $raw)) throw new Exception('URL server harus diawali http:// atau https://');
    return rtrim($raw, '/');
}

function api_endpoint($cfg, $raw, $suffix) {
    $base = resolve_api_base($cfg, $raw);
    if (substr($base, -strlen($suffix)) === $suffix) return $base;
    return $base . $suffix;
}

function save_ngrok($cfg, $url) {
    $state = load_state($cfg);
    $state['ngrok_url'] = $url;
    save_state($cfg, $state);
}

function read_upload($field, $exts, $label) {
    if (!isset($_FILES[$field]) || !is_uploaded_file($_FILES[$field]['tmp_name'])) {
        throw new Exception("File {$label} wajib diunggah.");
    }
    $name = $_FILES[$field]['name'];
    $ext = strtolower(pathinfo($name, PATHINFO_EXTENSION));
    if ($exts && !in_array($ext, $exts, true)) {
        throw new Exception("File {$label} harus berformat " . implode('/', $exts) . '.');
    }
    return [file_get_contents($_FILES[$field]['tmp_name']), $name];
}

// Ekstrak Training Phrase & Intent dari ZIP hasil Step 3.
function extract_training_intent($zipBytes) {
    $tmp = tempnam(sys_get_temp_dir(), 'z');
    file_put_contents($tmp, $zipBytes);
    $zip = new ZipArchive();
    if ($zip->open($tmp) !== true) { @unlink($tmp); throw new Exception('Gagal membaca ZIP hasil Step 3.'); }
    $train = $content = null;
    for ($i = 0; $i < $zip->numFiles; $i++) {
        $name = $zip->getNameIndex($i);
        if (strtolower(pathinfo($name, PATHINFO_EXTENSION)) !== 'xlsx') continue;
        if ($train === null && stripos($name, 'Training') !== false) { $train = $zip->getFromIndex($i); continue; }
        if ($content === null && stripos($name, 'Intent') !== false) { $content = $zip->getFromIndex($i); }
    }
    $zip->close();
    @unlink($tmp);
    if ($train === null) throw new Exception("File 'Training Phrase' tidak ada di ZIP Step 3.");
    if ($content === null) throw new Exception("File 'Intent' tidak ada di ZIP Step 3.");
    return [$train, $content];
}

function curl_common_headers($apiKey, $extra = []) {
    return array_merge(['X-API-Key: ' . $apiKey, 'ngrok-skip-browser-warning: true'], $extra);
}

// POST multipart, kembalikan body MENTAH (tak paksa PK/JSON). $files: [field => [bytes,name,mime?]].
function curl_multipart_raw($endpoint, $apiKey, $files, $fields = [], &$outHeaders = null, &$httpCode = null) {
    $post = []; $tmps = [];
    foreach ($files as $field => $info) {
        $t = tempnam(sys_get_temp_dir(), 'up');
        file_put_contents($t, $info[0]);
        $tmps[] = $t;
        $mime = isset($info[2]) ? $info[2] : 'application/octet-stream';
        $post[$field] = new CURLFile($t, $mime, $info[1]);
    }
    foreach ($fields as $k => $v) { $post[$k] = $v; }
    $hdrs = [];
    $ch = curl_init($endpoint);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => $post,
        CURLOPT_HTTPHEADER => curl_common_headers($apiKey),
        CURLOPT_TIMEOUT => 3600,
        CURLOPT_HEADERFUNCTION => function ($ch, $line) use (&$hdrs) {
            $p = strpos($line, ':');
            if ($p !== false) { $hdrs[strtolower(trim(substr($line, 0, $p)))] = trim(substr($line, $p + 1)); }
            return strlen($line);
        },
    ]);
    $res = curl_exec($ch);
    $outHeaders = $hdrs;
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err = curl_error($ch);
    curl_close($ch);
    foreach ($tmps as $t) { @unlink($t); }
    if ($res === false) throw new Exception('Gagal menghubungi server Colab: ' . $err);
    if ($httpCode < 200 || $httpCode >= 300) throw new Exception('Server error (HTTP ' . $httpCode . '): ' . substr((string)$res, 0, 1500));
    return $res;
}

// POST JSON, kembalikan body mentah.
function curl_json_raw($endpoint, $apiKey, $jsonStr, &$outHeaders = null, &$httpCode = null) {
    $hdrs = [];
    $ch = curl_init($endpoint);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => $jsonStr,
        CURLOPT_HTTPHEADER => curl_common_headers($apiKey, ['Content-Type: application/json']),
        CURLOPT_TIMEOUT => 3600,
        CURLOPT_HEADERFUNCTION => function ($ch, $line) use (&$hdrs) {
            $p = strpos($line, ':');
            if ($p !== false) { $hdrs[strtolower(trim(substr($line, 0, $p)))] = trim(substr($line, $p + 1)); }
            return strlen($line);
        },
    ]);
    $res = curl_exec($ch);
    $outHeaders = $hdrs;
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err = curl_error($ch);
    curl_close($ch);
    if ($res === false) throw new Exception('Gagal menghubungi server Colab: ' . $err);
    if ($httpCode < 200 || $httpCode >= 300) throw new Exception('Server error (HTTP ' . $httpCode . '): ' . substr((string)$res, 0, 1500));
    return $res;
}

// GET sederhana (diagnostik).
function curl_get_raw($endpoint, $apiKey) {
    $ch = curl_init($endpoint);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HTTPHEADER => curl_common_headers($apiKey),
        CURLOPT_TIMEOUT => 60,
    ]);
    $res = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err = curl_error($ch);
    curl_close($ch);
    if ($res === false) throw new Exception('Gagal menghubungi server: ' . $err);
    if ($code < 200 || $code >= 300) throw new Exception('Server error (HTTP ' . $code . '): ' . substr((string)$res, 0, 1500));
    return $res;
}

// POST multipart beberapa file (+ field teks opsional) ke server, kembalikan bytes.
function curl_multipart($endpoint, $apiKey, $files, $fields = [], &$outHeaders = null) {
    $post = [];
    $tmps = [];
    foreach ($files as $field => $info) {
        $t = tempnam(sys_get_temp_dir(), 'up') . '.xlsx';
        file_put_contents($t, $info[0]);
        $tmps[] = $t;
        $post[$field] = new CURLFile($t, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', $info[1]);
    }
    foreach ($fields as $k => $v) { $post[$k] = $v; }
    $hdrs = [];
    $ch = curl_init($endpoint);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => $post,
        CURLOPT_HTTPHEADER => ['X-API-Key: ' . $apiKey, 'ngrok-skip-browser-warning: true'],
        CURLOPT_TIMEOUT => 3600,
        CURLOPT_HEADERFUNCTION => function ($ch, $line) use (&$hdrs) {
            $p = strpos($line, ':');
            if ($p !== false) { $hdrs[strtolower(trim(substr($line, 0, $p)))] = trim(substr($line, $p + 1)); }
            return strlen($line);
        },
    ]);
    $res = curl_exec($ch);
    $outHeaders = $hdrs;
    $status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $ctype = curl_getinfo($ch, CURLINFO_CONTENT_TYPE);
    $err = curl_error($ch);
    curl_close($ch);
    foreach ($tmps as $t) { @unlink($t); }
    if ($res === false) throw new Exception('Gagal menghubungi server Colab: ' . $err);
    if ($status < 200 || $status >= 300) throw new Exception('Server error (HTTP ' . $status . '): ' . substr((string)$res, 0, 300));
    if (stripos((string)$ctype, 'json') !== false) {
        throw new Exception('Server membalas JSON, bukan file XLSX: ' . substr((string)$res, 0, 300));
    }
    // File XLSX/ZIP selalu diawali signature "PK". Jika bukan, itu HTML/teks error.
    if (substr((string)$res, 0, 2) !== 'PK') {
        $peek = trim(preg_replace('/\s+/', ' ', strip_tags((string)$res)));
        throw new Exception('Server tidak mengembalikan file XLSX yang valid (mungkin halaman error/interstitial). Cuplikan: ' . substr($peek, 0, 300));
    }
    return $res;
}

/*==================================================================
| STEP 4 — Analisis Rekomendasi Fallback (SBERT + BGE) via /api/analyze-fallback
|  3 file: workbook utama (Step 2, sheet "Fallback") + Training Phrase + Intent
|  (Step 3 ZIP). Output: XLSX Top-5 (sheet "LLM Fallback").
*=================================================================*/
function step4_analyze($cfg) {
    $rawBase = isset($_POST['ngrok_url']) ? trim($_POST['ngrok_url']) : '';
    $endpoint = api_endpoint($cfg, $rawBase, '/api/analyze-fallback');
    if (empty($cfg['force_local_api']) && $rawBase !== '') save_ngrok($cfg, $rawBase);

    $mode = isset($_POST['mode']) ? $_POST['mode'] : 'auto';

    if ($mode === 'manual') {
        list($mainBytes, $mainName) = read_upload('main_file', ['xlsx'], 'Workbook utama');
        list($trainBytes, ) = read_upload('training_file', ['xlsx'], 'Training Phrase');
        list($contentBytes, ) = read_upload('content_file', ['xlsx'], 'Intent');
    } else {
        $state = load_state($cfg);
        $s2 = isset($state['steps']['2']) ? $state['steps']['2'] : null;
        if (!$s2 || empty($s2['file'])) throw new Exception('Hasil Step 2 belum ada. Jalankan Step 2 dulu.');
        $mainPath = run_dir($cfg) . '/' . $s2['file'];
        if (!is_file($mainPath)) throw new Exception('File hasil Step 2 hilang dari server.');
        $mainBytes = file_get_contents($mainPath);
        $mainName = $s2['name'];

        $s3 = isset($state['steps']['3']) ? $state['steps']['3'] : null;
        if (!$s3 || empty($s3['file'])) throw new Exception('Hasil Step 3 belum ada. Jalankan Step 3 dulu.');
        $zipPath = run_dir($cfg) . '/' . $s3['file'];
        if (!is_file($zipPath)) throw new Exception('File hasil Step 3 hilang dari server.');
        list($trainBytes, $contentBytes) = extract_training_intent(file_get_contents($zipPath));
    }

    $result = curl_multipart($endpoint, $cfg['qwen_api_key'], [
        'file'          => [$mainBytes, $mainName ?: 'main.xlsx'],
        'file_training' => [$trainBytes, 'Analisis Fallback - Training Phrase.xlsx'],
        'file_content'  => [$contentBytes, 'Analisis Fallback - Intent.xlsx'],
    ]);

    $summary = [
        'status' => 'Selesai',
        'endpoint' => $endpoint,
        'sumber' => $mode === 'manual' ? 'Unggah 3 file' : 'Otomatis (Step 2 + Step 3)',
        'output_size' => strlen($result),
    ];
    $data = save_artifact($cfg, 4, 'xlsx', $result, 'hasil_top5_hybrid.xlsx', $summary);
    return ['step' => 4, 'artifact' => $data];
}

/*==================================================================
| STEP 5 — Qwen Judgement Top 5 via /api/judge-xlsx
|  Input XLSX Top-5 dari Step 4 (atau unggah). Server & URL sama dgn Step 4.
*=================================================================*/
function step5_qwen_judge($cfg) {
    $rawBase = isset($_POST['ngrok_url']) ? trim($_POST['ngrok_url']) : '';
    if ($rawBase === '') {
        $state = load_state($cfg);
        $rawBase = isset($state['ngrok_url']) ? $state['ngrok_url'] : '';
    }
    $endpoint = api_endpoint($cfg, $rawBase, '/api/judge-xlsx');
    if (empty($cfg['force_local_api']) && $rawBase !== '') save_ngrok($cfg, $rawBase);

    list($bytes, $origName) = resolve_input_bytes($cfg, 'xlsx_file', ['xlsx']);

    $result = curl_multipart($endpoint, $cfg['qwen_api_key'], [
        'file' => [$bytes, $origName ?: 'hasil_top5_hybrid.xlsx'],
    ]);

    $base2 = preg_replace('/\.xlsx$/i', '', $origName ?: 'hasil_top5_hybrid');
    $summary = [
        'status' => 'Selesai',
        'endpoint' => $endpoint,
        'source_file' => $origName,
        'output_size' => strlen($result),
    ];
    $data = save_artifact($cfg, 5, 'xlsx', $result, $base2 . '_judged.xlsx', $summary);
    return ['step' => 5, 'artifact' => $data];
}

/*==================================================================
| STEP 6 — Cross-check manual (baca & tulis sheet "Analisis Fallback")
|  Membaca hasil Step 5, menampilkan untuk dikoreksi manusia, lalu menulis
|  ulang XLSX yang sama dengan kolom "Intent Judgement LLM" & "Isi Intent"
|  hasil koreksi manusia.
*=================================================================*/
function xlsx_col_index($letters) {
    $n = 0; $len = strlen($letters);
    for ($i = 0; $i < $len; $i++) { $n = $n * 26 + (ord($letters[$i]) - 64); }
    return $n;
}

function xlsx_xml_decode($s) {
    return html_entity_decode($s, ENT_QUOTES | ENT_XML1, 'UTF-8');
}

function xlsx_zip_open($path) {
    $zip = new ZipArchive();
    if ($zip->open($path) !== true) throw new Exception('Gagal membuka file XLSX.');
    return $zip;
}

function xlsx_shared_strings($zip) {
    $xml = $zip->getFromName('xl/sharedStrings.xml');
    if ($xml === false) return [];
    $out = [];
    if (preg_match_all('#<si>(.*?)</si>#s', $xml, $m)) {
        foreach ($m[1] as $si) {
            if (preg_match_all('#<t[^>]*>(.*?)</t>#s', $si, $tm)) {
                $out[] = xlsx_xml_decode(implode('', $tm[1]));
            } else {
                $out[] = '';
            }
        }
    }
    return $out;
}

// Peta nama sheet -> path file worksheet (via workbook.xml + rels).
function xlsx_sheet_files($zip) {
    $wb = $zip->getFromName('xl/workbook.xml');
    $rels = $zip->getFromName('xl/_rels/workbook.xml.rels');
    $ridToTarget = [];
    // Urutan atribut TIDAK dijamin (openpyxl menulis Target sebelum Id).
    if ($rels && preg_match_all('#<Relationship\b[^>]*>#', $rels, $rm)) {
        foreach ($rm[0] as $tag) {
            if (preg_match('#\bId="([^"]+)"#', $tag, $idm) && preg_match('#\bTarget="([^"]+)"#', $tag, $tgm)) {
                $t = $tgm[1];
                if ($t !== '' && $t[0] === '/') { $t = ltrim($t, '/'); }
                elseif (strpos($t, 'xl/') !== 0) { $t = 'xl/' . ltrim($t, './'); }
                $ridToTarget[$idm[1]] = $t;
            }
        }
    }
    $map = [];
    if ($wb && preg_match_all('#<sheet\b[^>]*>#', $wb, $sm)) {
        foreach ($sm[0] as $tag) {
            if (preg_match('#\bname="([^"]+)"#', $tag, $nm) && preg_match('#\br:id="([^"]+)"#', $tag, $rid)) {
                $name = xlsx_xml_decode($nm[1]);
                if (isset($ridToTarget[$rid[1]])) $map[$name] = $ridToTarget[$rid[1]];
            }
        }
    }
    return $map;
}

// Baca satu sheet -> ['rows'=>[rownum=>[colLetter=>value]], 'headers'=>[name=>col]].
function xlsx_read_sheet($zip, $sheetPath, $shared) {
    $xml = $zip->getFromName($sheetPath);
    if ($xml === false) throw new Exception('Sheet tidak ditemukan: ' . $sheetPath);
    $rows = [];
    if (preg_match_all('#<row[^>]*\br="([0-9]+)"[^>]*>(.*?)</row>#s', $xml, $rm, PREG_SET_ORDER)) {
        foreach ($rm as $rr) {
            $rownum = (int)$rr[1];
            $cells = [];
            if (preg_match_all('#<c\b([^>]*)(?:/>|>(.*?)</c>)#s', $rr[2], $cm, PREG_SET_ORDER)) {
                foreach ($cm as $cc) {
                    $attrs = $cc[1];
                    $inner = isset($cc[2]) ? $cc[2] : '';
                    if (!preg_match('#\br="([A-Z]+)[0-9]+"#', $attrs, $refm)) continue;
                    $col = $refm[1];
                    $t = '';
                    if (preg_match('#\bt="([^"]+)"#', $attrs, $tm)) $t = $tm[1];
                    $val = '';
                    if ($t === 'inlineStr') {
                        if (preg_match_all('#<t[^>]*>(.*?)</t>#s', $inner, $im)) $val = xlsx_xml_decode(implode('', $im[1]));
                    } elseif ($t === 's') {
                        if (preg_match('#<v>(.*?)</v>#s', $inner, $vm)) {
                            $idx = (int)$vm[1];
                            $val = isset($shared[$idx]) ? $shared[$idx] : '';
                        }
                    } else {
                        if (preg_match('#<v>(.*?)</v>#s', $inner, $vm)) $val = xlsx_xml_decode($vm[1]);
                    }
                    $cells[$col] = $val;
                }
            }
            $rows[$rownum] = $cells;
        }
    }
    $headers = [];
    if (isset($rows[1])) {
        foreach ($rows[1] as $col => $name) {
            $nm = trim((string)$name);
            if ($nm !== '') $headers[$nm] = $col;
        }
    }
    return ['rows' => $rows, 'headers' => $headers, 'maxRow' => $rows ? max(array_keys($rows)) : 0];
}

// Set nilai sebuah sel (col+row) menjadi inline string secara aman (str_replace).
function xlsx_set_cell_xml($sheetXml, $rowNum, $colLetter, $value) {
    $ref = $colLetter . $rowNum;
    $cellXml = '<c r="' . $ref . '" t="inlineStr"><is><t xml:space="preserve">' . xml_esc($value) . '</t></is></c>';
    $patternFull = '#<c r="' . $ref . '"[^>]*>.*?</c>#s';
    $patternSelf = '#<c r="' . $ref . '"[^>]*/>#s';
    if (preg_match($patternFull, $sheetXml, $mm)) return str_replace($mm[0], $cellXml, $sheetXml);
    if (preg_match($patternSelf, $sheetXml, $mm)) return str_replace($mm[0], $cellXml, $sheetXml);
    // Sel belum ada -> sisipkan ke baris pada urutan kolom yang benar.
    $rowFull = '#<row[^>]*\br="' . $rowNum . '"[^>]*>.*?</row>#s';
    if (preg_match($rowFull, $sheetXml, $mm)) {
        $whole = $mm[0];
        if (preg_match('#^(<row[^>]*>)(.*)(</row>)$#s', $whole, $rp)) {
            $list = [];
            if (preg_match_all('#<c\b[^>]*r="([A-Z]+)[0-9]+"[^>]*(?:/>|>.*?</c>)#s', $rp[2], $cm, PREG_SET_ORDER)) {
                foreach ($cm as $c) { $list[$c[1]] = $c[0]; }
            }
            $list[$colLetter] = $cellXml;
            uksort($list, function ($a, $b) { return xlsx_col_index($a) - xlsx_col_index($b); });
            $newRow = $rp[1] . implode('', $list) . $rp[3];
            return str_replace($whole, $newRow, $sheetXml);
        }
    }
    return $sheetXml;
}

// Sumber Step 6: unggahan > hasil Step 6 sebelumnya (agar edit tersimpan) > hasil Step 5.
function step6_source_bytes($cfg) {
    if (isset($_FILES['xlsx_file']) && is_uploaded_file($_FILES['xlsx_file']['tmp_name'])) {
        return file_get_contents($_FILES['xlsx_file']['tmp_name']);
    }
    $state = load_state($cfg);
    $s6 = isset($state['steps']['6']) ? $state['steps']['6'] : null;
    if ($s6 && !empty($s6['file'])) {
        $p = run_dir($cfg) . '/' . $s6['file'];
        if (is_file($p)) return file_get_contents($p);
    }
    $s5 = isset($state['steps']['5']) ? $state['steps']['5'] : null;
    if (!$s5 || empty($s5['file'])) throw new Exception('Hasil Step 5 belum ada. Jalankan Step 5 dulu.');
    $p = run_dir($cfg) . '/' . $s5['file'];
    if (!is_file($p)) throw new Exception('File hasil Step 5 hilang dari server.');
    return file_get_contents($p);
}

// LOAD: kirim baris untuk ditampilkan/diedit di modal.
function step6_load($cfg) {
    $bytes = step6_source_bytes($cfg);
    $srcPath = run_dir($cfg) . '/step6_source.xlsx';
    file_put_contents($srcPath, $bytes);

    $zip = xlsx_zip_open($srcPath);
    $shared = xlsx_shared_strings($zip);
    $sheetFiles = xlsx_sheet_files($zip);
    if (!isset($sheetFiles['Rekomendasi Fallback'])) { $zip->close(); throw new Exception('Sheet "Rekomendasi Fallback" tidak ada di file.'); }
    if (!isset($sheetFiles['Analisis Fallback'])) { $zip->close(); throw new Exception('Sheet "Analisis Fallback" tidak ada. Pastikan memakai hasil Step 5.'); }
    $llm = xlsx_read_sheet($zip, $sheetFiles['Rekomendasi Fallback'], $shared);
    $analisis = xlsx_read_sheet($zip, $sheetFiles['Analisis Fallback'], $shared);
    $zip->close();

    $H = $llm['headers'];
    $colQ = isset($H['Pertanyaan User']) ? $H['Pertanyaan User'] : null;
    $colIns = isset($H['InserId']) ? $H['InserId'] : (isset($H['InsertId']) ? $H['InsertId'] : null);
    $rek = [];
    for ($r = 1; $r <= 5; $r++) {
        $rek[$r] = [
            'id'   => isset($H["Rek_{$r}_ID"]) ? $H["Rek_{$r}_ID"] : null,
            'ans'  => isset($H["Rek_{$r}_Jawaban"]) ? $H["Rek_{$r}_Jawaban"] : null,
            'skor' => isset($H["Rek_{$r}_Skor_Deteksi"]) ? $H["Rek_{$r}_Skor_Deteksi"] : null,
            'conf' => isset($H["Rek_{$r}_Confidence"]) ? $H["Rek_{$r}_Confidence"] : null,
        ];
    }

    $llmByIns = [];
    $llmByOrder = [];
    foreach ($llm['rows'] as $rn => $cells) {
        if ($rn === 1) continue;
        $g = function ($c) use ($cells) { return ($c && isset($cells[$c])) ? (string)$cells[$c] : ''; };
        $opts = [];
        for ($r = 1; $r <= 5; $r++) {
            $id = trim($g($rek[$r]['id']));
            if ($id === '') continue;
            $opts[] = ['id' => $id, 'ans' => $g($rek[$r]['ans']), 'skor' => $g($rek[$r]['skor']), 'conf' => $g($rek[$r]['conf'])];
        }
        if (!$opts && trim($g($colQ)) === '') continue; // baris kosong
        $obj = ['pertanyaan' => $g($colQ), 'options' => $opts];
        $ins = trim($g($colIns));
        if ($ins !== '') $llmByIns[$ins] = $obj;
        $llmByOrder[] = $obj;
    }

    $AH = $analisis['headers'];
    $aQ = isset($AH['Pertanyaan User']) ? $AH['Pertanyaan User'] : null;
    $aCat = isset($AH['Catatan LLM']) ? $AH['Catatan LLM'] : null;
    $aIntent = isset($AH['Intent Judgement LLM']) ? $AH['Intent Judgement LLM'] : null;
    $aIns = isset($AH['InsertId']) ? $AH['InsertId'] : (isset($AH['InserId']) ? $AH['InserId'] : null);

    $out = [];
    $order = 0;
    foreach ($analisis['rows'] as $rn => $cells) {
        if ($rn === 1) continue;
        $g = function ($c) use ($cells) { return ($c && isset($cells[$c])) ? (string)$cells[$c] : ''; };
        $ins = trim($g($aIns));
        $pert = $g($aQ);
        $cat = $g($aCat);
        $intent = $g($aIntent);
        if ($pert === '' && $intent === '' && $ins === '') continue;
        $match = ($ins !== '' && isset($llmByIns[$ins])) ? $llmByIns[$ins] : (isset($llmByOrder[$order]) ? $llmByOrder[$order] : null);
        $opts = $match ? $match['options'] : [];
        $skor = ''; $conf = '';
        foreach ($opts as $o) { if ($o['id'] === $intent) { $skor = $o['skor']; $conf = $o['conf']; break; } }
        $out[] = [
            'row' => $rn,
            'pertanyaan' => $pert,
            'catatan' => $cat,
            'intent' => $intent,
            'skor' => $skor,
            'conf' => $conf,
            'options' => $opts,
        ];
        $order++;
    }

    return ['step' => 6, 'rows' => $out, 'total' => count($out)];
}

// SAVE: terapkan koreksi manusia ke sheet "Analisis Fallback", hasilkan XLSX baru.
function step6_save($cfg) {
    $editsRaw = isset($_POST['edits']) ? $_POST['edits'] : '';
    $edits = json_decode($editsRaw, true);
    if (!is_array($edits)) throw new Exception('Data edit tidak valid.');

    $srcPath = run_dir($cfg) . '/step6_source.xlsx';
    if (!is_file($srcPath)) { file_put_contents($srcPath, step6_source_bytes($cfg)); }
    $work = run_dir($cfg) . '/step6_work.xlsx';
    copy($srcPath, $work);

    $zip = xlsx_zip_open($work);
    $shared = xlsx_shared_strings($zip);
    $sheetFiles = xlsx_sheet_files($zip);
    if (!isset($sheetFiles['Analisis Fallback'])) { $zip->close(); throw new Exception('Sheet "Analisis Fallback" tidak ada.'); }
    $sheetPath = $sheetFiles['Analisis Fallback'];
    $sheet = xlsx_read_sheet($zip, $sheetPath, $shared);
    $AH = $sheet['headers'];
    $colIntent = isset($AH['Intent Judgement LLM']) ? $AH['Intent Judgement LLM'] : null;
    $colIsi = isset($AH['Isi Intent']) ? $AH['Isi Intent'] : null;
    if (!$colIntent) { $zip->close(); throw new Exception('Kolom "Intent Judgement LLM" tidak ditemukan.'); }
    $sheetXml = $zip->getFromName($sheetPath);

    $applied = 0;
    foreach ($edits as $e) {
        $row = isset($e['row']) ? (int)$e['row'] : 0;
        if ($row < 2) continue;
        $intent = isset($e['intent']) ? (string)$e['intent'] : '';
        $isi = isset($e['isi']) ? (string)$e['isi'] : '';
        $sheetXml = xlsx_set_cell_xml($sheetXml, $row, $colIntent, $intent);
        if ($colIsi) $sheetXml = xlsx_set_cell_xml($sheetXml, $row, $colIsi, $isi);
        $applied++;
    }

    $zip->deleteName($sheetPath);
    $zip->addFromString($sheetPath, $sheetXml);
    if ($zip->locateName('xl/calcChain.xml') !== false) { $zip->deleteName('xl/calcChain.xml'); }
    $zip->close();

    $outBytes = file_get_contents($work);
    @unlink($work);
    $summary = ['status' => 'Selesai (disesuaikan manusia)', 'baris_diperbarui' => $applied];
    $data = save_artifact($cfg, 6, 'xlsx', $outBytes, 'hasil_final_manual.xlsx', $summary);
    return ['step' => 6, 'artifact' => $data];
}

/*==================================================================
| STEP 7 — Analisis MKTA (Match Konten Tidak Akurat) via /api/mkta-analyze
|  Kirim workbook (sheet "Non Fallback") ke Colab; balik + sheet "Analisis MKTA".
*=================================================================*/
function step7_mkta($cfg) {
    $rawBase = isset($_POST['ngrok_url']) ? trim($_POST['ngrok_url']) : '';
    $endpoint = api_endpoint($cfg, $rawBase, '/api/mkta-analyze');
    if (empty($cfg['force_local_api']) && $rawBase !== '') save_ngrok($cfg, $rawBase);

    $mode = isset($_POST['mode']) ? $_POST['mode'] : 'auto';
    if ($mode === 'manual') {
        list($bytes, $name) = read_upload('xlsx_file', ['xlsx'], 'Workbook (punya sheet Non Fallback)');
    } else {
        $state = load_state($cfg);
        $pick = null;
        foreach (['6', '5', '4', '2'] as $s) {
            $st = isset($state['steps'][$s]) ? $state['steps'][$s] : null;
            if ($st && !empty($st['file']) && strtolower($st['ext']) === 'xlsx') { $pick = $st; break; }
        }
        if (!$pick) throw new Exception('Belum ada hasil ber-sheet "Non Fallback". Jalankan minimal Step 2 (idealnya sampai Step 6).');
        $p = run_dir($cfg) . '/' . $pick['file'];
        if (!is_file($p)) throw new Exception('File sumber hilang dari server.');
        $bytes = file_get_contents($p);
        $name = $pick['name'];
    }

    $files = ['file' => [$bytes, $name ?: 'input.xlsx']];
    // MKTA v2: lampirkan katalog intent (Training Phrase + Isi Intent) dari hasil
    // Step 3 bila tersedia, agar analyzer bisa menghitung sinyal intent-space
    // (relevansi intent bot, intent terdekat, kandidat, kategori). Bersifat OPSIONAL:
    // bila katalog tidak ada, Step 7 tetap jalan (mode answerability saja).
    try {
        $stCat = isset($state) ? $state : load_state($cfg);
        $s3 = isset($stCat['steps']['3']) ? $stCat['steps']['3'] : null;
        if ($s3 && !empty($s3['file'])) {
            $zp = run_dir($cfg) . '/' . $s3['file'];
            if (is_file($zp)) {
                list($trainBytes, $contentBytes) = extract_training_intent(file_get_contents($zp));
                $files['file_training'] = [$trainBytes, 'training_phrase.xlsx'];
                $files['file_content']  = [$contentBytes, 'intent_content.xlsx'];
            }
        }
    } catch (Exception $e) {
        // katalog opsional; lanjut tanpa sinyal intent-space
    }

    $result = curl_multipart($endpoint, $cfg['qwen_api_key'], $files);

    $summary = ['status' => 'Selesai', 'endpoint' => $endpoint, 'output_size' => strlen($result)];
    $data = save_artifact($cfg, 7, 'xlsx', $result, 'hasil_analisis_mkta.xlsx', $summary);
    return ['step' => 7, 'artifact' => $data];
}

/*==================================================================
| STEP 8 — Putusan LLM MKTA (filter QA Conf -> Qwen)
|  step8load: hitung jumlah baris per ambang QA Conf (dari hasil Step 7).
|  step8    : kirim workbook + threshold ke /api/mkta-verdict, tulis PUTUSAN/ALASAN.
*=================================================================*/
function step8_thresholds() {
    return [0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8];
}

function step8_clear_artifact($cfg) {
    $state = load_state($cfg);
    if (isset($state['steps']['8'])) {
        $f = isset($state['steps']['8']['file']) ? $state['steps']['8']['file'] : '';
        if ($f) @unlink(run_dir($cfg) . '/' . $f);
        unset($state['steps']['8']);
        save_state($cfg, $state);
    }
}

function step8_counts_from_path($cfg, $path) {
    $zip = xlsx_zip_open($path);
    $shared = xlsx_shared_strings($zip);
    $sf = xlsx_sheet_files($zip);
    if (!isset($sf['QA Conf MKTA'])) { $zip->close(); throw new Exception('Sheet "QA Conf MKTA" tidak ada. Pakai hasil Step 7 (versi terbaru).'); }
    $sheet = xlsx_read_sheet($zip, $sf['QA Conf MKTA'], $shared);
    $zip->close();
    $col = isset($sheet['headers']['Skor Pemrosesan Bahasa']) ? $sheet['headers']['Skor Pemrosesan Bahasa'] : null;
    if (!$col) throw new Exception('Kolom "Skor Pemrosesan Bahasa" tidak ada di sheet QA Conf MKTA.');
    $scores = [];
    foreach ($sheet['rows'] as $rn => $cells) {
        if ($rn === 1) continue;
        if (!isset($cells[$col])) continue;
        $v = $cells[$col];
        if ($v === '' || !is_numeric($v)) continue;
        $scores[] = (float)$v;
    }
    $counts = [];
    foreach (step8_thresholds() as $t) {
        $c = 0;
        foreach ($scores as $s) { if ($s < $t) $c++; }
        $counts[] = ['th' => $t, 'count' => $c];
    }
    return ['total' => count($scores), 'counts' => $counts];
}

function step8_load($cfg) {
    $mode = isset($_POST['mode']) ? $_POST['mode'] : 'auto';
    $srcUpload = run_dir($cfg) . '/step8_source.xlsx';
    if ($mode === 'manual') {
        if (isset($_FILES['xlsx_file']) && is_uploaded_file($_FILES['xlsx_file']['tmp_name'])) {
            $nm = strtolower($_FILES['xlsx_file']['name']);
            if (substr($nm, -5) !== '.xlsx') throw new Exception('File harus berformat .xlsx');
            file_put_contents($srcUpload, file_get_contents($_FILES['xlsx_file']['tmp_name']));
            step8_clear_artifact($cfg); // mulai dari file unggahan yang baru
        }
        if (!is_file($srcUpload)) throw new Exception('Unggah file XLSX (ber-sheet "QA Conf MKTA") lalu klik Muat data.');
        $path = $srcUpload;
    } else {
        if (is_file($srcUpload)) @unlink($srcUpload);
        $state = load_state($cfg);
        $s7 = isset($state['steps']['7']) ? $state['steps']['7'] : null;
        if (!$s7 || empty($s7['file'])) throw new Exception('Hasil Step 7 belum ada. Jalankan Step 7 dulu.');
        $path = run_dir($cfg) . '/' . $s7['file'];
        if (!is_file($path)) throw new Exception('File hasil Step 7 hilang dari server.');
    }
    $res = step8_counts_from_path($cfg, $path);
    return ['step' => 8, 'mode' => $mode, 'total' => $res['total'], 'counts' => $res['counts']];
}

// Hitung status verdict dari sebuah file: berapa PUTUSAN terisi & berapa sisa target.
function step8_verdict_stats($path, $threshold) {
    $zip = xlsx_zip_open($path);
    $shared = xlsx_shared_strings($zip);
    $sf = xlsx_sheet_files($zip);
    if (!isset($sf['QA Conf MKTA'])) { $zip->close(); throw new Exception('Sheet "QA Conf MKTA" tidak ada.'); }
    $sheet = xlsx_read_sheet($zip, $sf['QA Conf MKTA'], $shared);
    $zip->close();
    $H = $sheet['headers'];
    $cs = isset($H['Skor Pemrosesan Bahasa']) ? $H['Skor Pemrosesan Bahasa'] : null;
    $cp = isset($H['PUTUSAN']) ? $H['PUTUSAN'] : null;
    if (!$cs) throw new Exception('Kolom "Skor Pemrosesan Bahasa" tidak ada di sheet QA Conf MKTA.');
    $filled = 0; $remaining = 0;
    foreach ($sheet['rows'] as $rn => $cells) {
        if ($rn === 1) continue;
        $sv = isset($cells[$cs]) ? $cells[$cs] : '';
        if ($sv === '' || !is_numeric($sv)) continue;
        $put = ($cp && isset($cells[$cp])) ? trim((string)$cells[$cp]) : '';
        if ($put !== '') $filled++;
        if ((float)$sv < $threshold && $put === '') $remaining++;
    }
    return ['filled' => $filled, 'remaining' => $remaining];
}

function step8_run($cfg) {
    $rawBase = isset($_POST['ngrok_url']) ? trim($_POST['ngrok_url']) : '';
    $endpoint = api_endpoint($cfg, $rawBase, '/api/mkta-verdict');
    if (empty($cfg['force_local_api']) && $rawBase !== '') save_ngrok($cfg, $rawBase);

    $threshold = isset($_POST['threshold']) ? (float)$_POST['threshold'] : 0.6;
    if ($threshold <= 0 || $threshold > 1) throw new Exception('Threshold tidak valid.');
    $limit = isset($cfg['mkta_chunk']) ? (int)$cfg['mkta_chunk'] : 12;

    // Basis (untuk lanjutan inkremental antar-chunk):
    // hasil Step 8 sebelumnya > file unggahan manual > hasil Step 7.
    $state = load_state($cfg);
    $basePath = null; $baseName = 'input.xlsx';
    if (isset($state['steps']['8']) && !empty($state['steps']['8']['file'])) {
        $p = run_dir($cfg) . '/' . $state['steps']['8']['file'];
        if (is_file($p)) { $basePath = $p; $baseName = $state['steps']['8']['name']; }
    }
    if (!$basePath) {
        $srcUpload = run_dir($cfg) . '/step8_source.xlsx';
        if (is_file($srcUpload)) { $basePath = $srcUpload; $baseName = 'qa_conf_mkta.xlsx'; }
    }
    if (!$basePath) {
        $s7 = isset($state['steps']['7']) ? $state['steps']['7'] : null;
        if (!$s7 || empty($s7['file'])) throw new Exception('Hasil Step 7 belum ada.');
        $p = run_dir($cfg) . '/' . $s7['file'];
        if (!is_file($p)) throw new Exception('File hasil Step 7 hilang dari server.');
        $basePath = $p; $baseName = $s7['name'];
    }
    $bytes = file_get_contents($basePath);

    // PUTUSAN terisi SEBELUM proses (untuk hitung berapa yg baru diproses).
    $prevFilled = 0;
    try { $ps = step8_verdict_stats($basePath, $threshold); $prevFilled = $ps['filled']; } catch (Throwable $e) { $prevFilled = 0; }

    $hdr = null;
    $result = curl_multipart($endpoint, $cfg['qwen_api_key'],
        ['file' => [$bytes, $baseName ?: 'input.xlsx']],
        ['threshold' => (string)$threshold, 'limit' => (string)$limit],
        $hdr
    );

    // Hitung otoritatif dari FILE HASIL (tidak bergantung header respons).
    $tmp = tempnam(sys_get_temp_dir(), 's8r') . '.xlsx';
    file_put_contents($tmp, $result);
    $after = step8_verdict_stats($tmp, $threshold);
    @unlink($tmp);
    $processed = max(0, $after['filled'] - $prevFilled);
    $remaining = $after['remaining'];
    // Fallback ke header bila pembacaan file gagal memberi angka.
    if ($processed === 0 && isset($hdr['x-processed'])) $processed = (int)$hdr['x-processed'];

    $summary = ['status' => ($remaining <= 0 ? 'Selesai' : 'Berlangsung...'), 'endpoint' => $endpoint, 'threshold' => $threshold, 'sisa' => $remaining];
    $data = save_artifact($cfg, 8, 'xlsx', $result, 'hasil_mkta_putusan.xlsx', $summary);
    return ['step' => 8, 'artifact' => $data, 'processed' => $processed, 'remaining' => $remaining, 'done' => ($remaining <= 0)];
}

/*==================================================================
| STEP 9 — Analisis Manual MKTA (analis mengisi 'Intent Seharusnya')
|  Membuat/menimpa sheet 'Analisis MKTA' berisi baris QA Conf < ambang pilihan
|  analis, dengan kolom: Pertanyaan User, Bot Response, Intent Name,
|  Skor Dialogflow, Skor Pemrosesan Bahasa, Skor NLI, PUTUSAN, Intent Seharusnya.
*=================================================================*/
function xlsx_plain_cell($ref, $val) {
    if (is_int($val) || is_float($val)) return '<c r="' . $ref . '"><v>' . $val . '</v></c>';
    if ($val === '' || $val === null) return '<c r="' . $ref . '"/>';
    return '<c r="' . $ref . '" t="inlineStr"><is><t xml:space="preserve">' . xml_esc($val) . '</t></is></c>';
}

function xlsx_plain_sheet_xml($aoa) {
    $x = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';
    $x .= '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetData>';
    foreach ($aoa as $ri => $row) {
        $rn = $ri + 1;
        $x .= '<row r="' . $rn . '">';
        foreach ($row as $ci => $v) { $x .= xlsx_plain_cell(xlsx_col($ci + 1) . $rn, $v); }
        $x .= '</row>';
    }
    $x .= '</sheetData></worksheet>';
    return $x;
}

// Buat/timpa sebuah worksheet di dalam xlsx yang sudah ada; kembalikan bytes baru.
function xlsx_upsert_sheet($srcPath, $sheetName, $aoa) {
    $RNS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
    $work = tempnam(sys_get_temp_dir(), 'up9') . '.xlsx';
    copy($srcPath, $work);
    $zip = new ZipArchive();
    if ($zip->open($work) !== true) { @unlink($work); throw new Exception('Gagal membuka workbook untuk menambah sheet.'); }
    $wbx = $zip->getFromName('xl/workbook.xml');
    $rels = $zip->getFromName('xl/_rels/workbook.xml.rels');
    $ct = $zip->getFromName('[Content_Types].xml');
    $ws = xlsx_plain_sheet_xml($aoa);

    $exists = null;
    if (preg_match('#<sheet\b[^>]*name="' . preg_quote($sheetName, '#') . '"[^>]*>#', $wbx, $m)) $exists = $m[0];

    if ($exists !== null) {
        preg_match('#r:id="([^"]+)"#', $exists, $rm);
        $rid = isset($rm[1]) ? $rm[1] : '';
        $target = null;
        if (preg_match_all('#<Relationship\b[^>]*>#', $rels, $rr)) {
            foreach ($rr[0] as $tag) {
                if (preg_match('#\bId="' . preg_quote($rid, '#') . '"#', $tag) && preg_match('#Target="([^"]+)"#', $tag, $tm)) { $target = $tm[1]; break; }
            }
        }
        if ($target === null) { $zip->close(); @unlink($work); throw new Exception('Target sheet lama tidak ditemukan.'); }
        $target = ($target[0] === '/') ? ltrim($target, '/') : ('xl/' . ltrim($target, './'));
        $zip->deleteName($target);
        $zip->addFromString($target, $ws);
    } else {
        $nums = [];
        if (preg_match_all('#worksheets/sheet(\d+)\.xml#', $rels, $nn)) $nums = array_map('intval', $nn[1]);
        $newnum = ($nums ? max($nums) : 0) + 1;
        $ridnums = [];
        if (preg_match_all('#Id="rId(\d+)"#', $rels, $rn2)) $ridnums = array_map('intval', $rn2[1]);
        $newrid = 'rId' . (($ridnums ? max($ridnums) : 0) + 1);
        $sids = [];
        if (preg_match_all('#sheetId="(\d+)"#', $wbx, $si)) $sids = array_map('intval', $si[1]);
        $newsid = ($sids ? max($sids) : 0) + 1;
        $newfile = 'xl/worksheets/sheet' . $newnum . '.xml';
        $zip->addFromString($newfile, $ws);
        $wbx = str_replace('</sheets>', '<sheet xmlns:r="' . $RNS . '" name="' . xml_esc($sheetName) . '" sheetId="' . $newsid . '" r:id="' . $newrid . '"/></sheets>', $wbx);
        $rels = str_replace('</Relationships>', '<Relationship Id="' . $newrid . '" Type="' . $RNS . '/worksheet" Target="worksheets/sheet' . $newnum . '.xml"/></Relationships>', $rels);
        $ct = str_replace('</Types>', '<Override PartName="/xl/worksheets/sheet' . $newnum . '.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>', $ct);
        $zip->deleteName('xl/workbook.xml'); $zip->addFromString('xl/workbook.xml', $wbx);
        $zip->deleteName('xl/_rels/workbook.xml.rels'); $zip->addFromString('xl/_rels/workbook.xml.rels', $rels);
        $zip->deleteName('[Content_Types].xml'); $zip->addFromString('[Content_Types].xml', $ct);
    }
    $zip->close();
    $bytes = file_get_contents($work);
    @unlink($work);
    return $bytes;
}

function step9_base_path($cfg) {
    $state = load_state($cfg);
    if (isset($state['steps']['9']) && !empty($state['steps']['9']['file'])) {
        $p = run_dir($cfg) . '/' . $state['steps']['9']['file'];
        if (is_file($p)) return [$p, $state['steps']['9']['name']];
    }
    if (isset($state['steps']['8']) && !empty($state['steps']['8']['file'])) {
        $p = run_dir($cfg) . '/' . $state['steps']['8']['file'];
        if (is_file($p)) return [$p, $state['steps']['8']['name']];
    }
    throw new Exception('Hasil Step 8 (putusan Qwen) belum ada. Jalankan Step 8 dulu.');
}

function step9_prior_map($am) {
    $H = $am['headers'];
    $cq = isset($H['Pertanyaan User']) ? $H['Pertanyaan User'] : null;
    $cb = isset($H['Bot Response']) ? $H['Bot Response'] : null;
    $cse = isset($H['Intent Seharusnya']) ? $H['Intent Seharusnya'] : null;
    $map = [];
    if (!$cq || !$cb || !$cse) return $map;
    foreach ($am['rows'] as $rn => $cells) {
        if ($rn === 1) continue;
        $p = isset($cells[$cq]) ? (string)$cells[$cq] : '';
        $b = isset($cells[$cb]) ? (string)$cells[$cb] : '';
        $se = isset($cells[$cse]) ? trim((string)$cells[$cse]) : '';
        if ($se !== '') $map[$p . "\x1f" . $b] = $se;
    }
    return $map;
}

function step9_load($cfg) {
    list($path, $name) = step9_base_path($cfg);
    $zip = xlsx_zip_open($path);
    $shared = xlsx_shared_strings($zip);
    $sf = xlsx_sheet_files($zip);
    if (!isset($sf['QA Conf MKTA'])) { $zip->close(); throw new Exception('Sheet "QA Conf MKTA" tidak ada. Jalankan Step 7 & 8 dulu.'); }
    $qa = xlsx_read_sheet($zip, $sf['QA Conf MKTA'], $shared);
    $prior = [];
    if (isset($sf['Analisis MKTA'])) { $am = xlsx_read_sheet($zip, $sf['Analisis MKTA'], $shared); $prior = step9_prior_map($am); }
    $zip->close();
    $H = $qa['headers'];
    $cq = isset($H['Pertanyaan User']) ? $H['Pertanyaan User'] : null;
    $cb = isset($H['Bot Response']) ? $H['Bot Response'] : null;
    $ci = isset($H['Intent Name']) ? $H['Intent Name'] : null;
    $cdf = isset($H['Skor Dialogflow']) ? $H['Skor Dialogflow'] : null;
    $cs = isset($H['Skor Pemrosesan Bahasa']) ? $H['Skor Pemrosesan Bahasa'] : null;
    $cn = isset($H['Skor NLI']) ? $H['Skor NLI'] : null;
    $cp = isset($H['PUTUSAN']) ? $H['PUTUSAN'] : null;
    $ckat = isset($H['Kategori Mesin']) ? $H['Kategori Mesin'] : null;
    $cpri = isset($H['Prioritas Tinjau']) ? $H['Prioritas Tinjau'] : null;
    $ckan = isset($H['Kandidat Intent']) ? $H['Kandidat Intent'] : null;
    $cnn = isset($H['Intent Terdekat (Mesin)']) ? $H['Intent Terdekat (Mesin)'] : null;
    $cllm = isset($H['INTENT SEHARUSNYA']) ? $H['INTENT SEHARUSNYA'] : null;
    $cala = isset($H['ALASAN']) ? $H['ALASAN'] : null;
    if (!$cq || !$cb || !$cs) throw new Exception('Kolom wajib tidak lengkap di sheet QA Conf MKTA.');
    $rows = [];
    foreach ($qa['rows'] as $rn => $cells) {
        if ($rn === 1) continue;
        $g = function ($c) use ($cells) { return ($c && isset($cells[$c])) ? (string)$cells[$c] : ''; };
        $pert = $g($cq); $bot = $g($cb);
        if ($pert === '' && $bot === '') continue;
        $llmSeh = $g($cllm);
        $priorSeh = isset($prior[$pert . "\x1f" . $bot]) ? $prior[$pert . "\x1f" . $bot] : '';
        $rows[] = [
            'row' => $rn, 'pertanyaan' => $pert, 'bot' => $bot, 'intent' => $g($ci),
            'df' => $g($cdf), 'qa' => $g($cs), 'nli' => $g($cn), 'putusan' => $g($cp),
            'kategori' => $g($ckat), 'prioritas' => $g($cpri), 'kandidat' => $g($ckan),
            'terdekat' => $g($cnn), 'llm_seharusnya' => $llmSeh, 'alasan' => $g($cala),
            'seharusnya' => $priorSeh,
        ];
    }
    return ['step' => 9, 'rows' => $rows, 'total' => count($rows)];
}

function step9_save($cfg) {
    $threshold = isset($_POST['threshold']) ? (float)$_POST['threshold'] : 0.5;
    if ($threshold <= 0 || $threshold > 1) throw new Exception('Ambang QA Conf tidak valid (0-1).');
    $edits = json_decode(isset($_POST['edits']) ? $_POST['edits'] : '', true);
    if (!is_array($edits)) $edits = [];

    list($path, $name) = step9_base_path($cfg);
    $zip = xlsx_zip_open($path);
    $shared = xlsx_shared_strings($zip);
    $sf = xlsx_sheet_files($zip);
    if (!isset($sf['QA Conf MKTA'])) { $zip->close(); throw new Exception('Sheet "QA Conf MKTA" tidak ada.'); }
    $qa = xlsx_read_sheet($zip, $sf['QA Conf MKTA'], $shared);
    $prior = [];
    if (isset($sf['Analisis MKTA'])) { $am = xlsx_read_sheet($zip, $sf['Analisis MKTA'], $shared); $prior = step9_prior_map($am); }
    $zip->close();

    $H = $qa['headers'];
    $cq = isset($H['Pertanyaan User']) ? $H['Pertanyaan User'] : null;
    $cb = isset($H['Bot Response']) ? $H['Bot Response'] : null;
    $ci = isset($H['Intent Name']) ? $H['Intent Name'] : null;
    $cdf = isset($H['Skor Dialogflow']) ? $H['Skor Dialogflow'] : null;
    $cs = isset($H['Skor Pemrosesan Bahasa']) ? $H['Skor Pemrosesan Bahasa'] : null;
    $cn = isset($H['Skor NLI']) ? $H['Skor NLI'] : null;
    $cp = isset($H['PUTUSAN']) ? $H['PUTUSAN'] : null;
    $ckat = isset($H['Kategori Mesin']) ? $H['Kategori Mesin'] : null;
    $cpri = isset($H['Prioritas Tinjau']) ? $H['Prioritas Tinjau'] : null;
    $ckan = isset($H['Kandidat Intent']) ? $H['Kandidat Intent'] : null;
    $cnn = isset($H['Intent Terdekat (Mesin)']) ? $H['Intent Terdekat (Mesin)'] : null;
    $cllm = isset($H['INTENT SEHARUSNYA']) ? $H['INTENT SEHARUSNYA'] : null;
    $cala = isset($H['ALASAN']) ? $H['ALASAN'] : null;
    if (!$cs) throw new Exception('Kolom "Skor Pemrosesan Bahasa" tidak ada.');

    $header = ['Pertanyaan User', 'Bot Response', 'Intent Name', 'Skor Dialogflow', 'Skor Pemrosesan Bahasa', 'Skor NLI', 'Kategori Mesin', 'Prioritas Tinjau', 'Intent Terdekat (Mesin)', 'Kandidat Intent', 'PUTUSAN', 'Intent Seharusnya (LLM)', 'ALASAN', 'Intent Seharusnya'];
    $aoa = [$header];
    foreach ($qa['rows'] as $rn => $cells) {
        if ($rn === 1) continue;
        $g = function ($c) use ($cells) { return ($c && isset($cells[$c])) ? $cells[$c] : ''; };
        $qav = $g($cs);
        if ($qav === '' || !is_numeric($qav)) continue;
        if ((float)$qav >= $threshold) continue;
        $pert = (string)$g($cq); $bot = (string)$g($cb);
        if (isset($edits[(string)$rn])) $seh = (string)$edits[(string)$rn];
        elseif (isset($prior[$pert . "\x1f" . $bot])) $seh = $prior[$pert . "\x1f" . $bot];
        else $seh = '';
        $df = $g($cdf); $nli = $g($cn); $pri = $g($cpri);
        $aoa[] = [
            $pert, $bot, (string)$g($ci),
            (is_numeric($df) ? (float)$df : (string)$df),
            (float)$qav,
            (is_numeric($nli) ? (float)$nli : (string)$nli),
            (string)$g($ckat),
            (is_numeric($pri) ? (float)$pri : (string)$pri),
            (string)$g($cnn), (string)$g($ckan),
            (string)$g($cp), (string)$g($cllm), (string)$g($cala), $seh,
        ];
    }
    if (count($aoa) === 1) throw new Exception('Tidak ada baris dengan QA Conf < ' . $threshold . '.');

    $out = xlsx_upsert_sheet($path, 'Analisis MKTA', $aoa);
    $summary = ['status' => 'Selesai', 'ambang_qa' => $threshold, 'baris' => count($aoa) - 1];
    $data = save_artifact($cfg, 9, 'xlsx', $out, 'hasil_analisis_mkta_manual.xlsx', $summary);
    return ['step' => 9, 'artifact' => $data, 'baris' => count($aoa) - 1];
}

/*==================================================================
| STEP 10 — Laporan: sheet LM + sheet Pembaruan, plus CSV LM & CSV Pembaruan.
|  Sumber: hasil Step 9 (memuat Non Fallback, Analisis Fallback, QA Conf MKTA,
|  Analisis MKTA). Hanya baris HASIL_LM = TINDAK LANJUT yang masuk laporan.
*=================================================================*/
function s10_find($headers, $cands) {
    foreach ($cands as $c) {
        foreach ($headers as $name => $idx) {
            if (strcasecmp(trim((string)$name), $c) === 0) return $idx;
        }
    }
    return null;
}

function s10_date($v) {
    $v = trim((string)$v);
    if ($v === '') return '';
    if (preg_match('/^(\d{4}-\d{2}-\d{2})/', $v, $m)) return $m[1];
    $t = strtotime($v);
    return $t ? date('Y-m-d', $t) : $v;
}

function s10_csv_cell($v) {
    $v = (string)$v;
    if (preg_match('/[",\n\r]/', $v)) return '"' . str_replace('"', '""', $v) . '"';
    return $v;
}
function s10_csv($aoa) {
    $lines = [];
    foreach ($aoa as $row) { $lines[] = implode(',', array_map('s10_csv_cell', $row)); }
    return implode("\r\n", $lines) . "\r\n";
}

function step10_build($cfg) {
    $state = load_state($cfg);
    $s9 = isset($state['steps']['9']) ? $state['steps']['9'] : null;
    if (!$s9 || empty($s9['file'])) throw new Exception('Hasil Step 9 belum ada. Jalankan Step 9 dulu.');
    $srcPath = run_dir($cfg) . '/' . $s9['file'];
    if (!is_file($srcPath)) throw new Exception('File hasil Step 9 hilang dari server.');

    $zip = xlsx_zip_open($srcPath);
    $shared = xlsx_shared_strings($zip);
    $sf = xlsx_sheet_files($zip);
    $read = function ($sheetName) use ($zip, $sf, $shared) {
        if (!isset($sf[$sheetName])) return null;
        return xlsx_read_sheet($zip, $sf[$sheetName], $shared);
    };
    $nonfb = $read('Non Fallback');
    $afb = $read('Analisis Fallback');
    $qa = $read('QA Conf MKTA');
    $amkta = $read('Analisis MKTA');
    $zip->close();

    // ---- Agregat per ID Rekaman ----
    $ID = [];  // id => ['nonfb'=>int,'umk'=>int,'mkta'=>int,'date'=>str,'fb'=>[], 'mk'=>[]]
    $ensure = function (&$ID, $id) {
        if (!isset($ID[$id])) $ID[$id] = ['nonfb' => 0, 'umk' => 0, 'mkta' => 0, 'date' => '', 'fb' => [], 'mk' => []];
    };

    // 1) Non Fallback -> total interaksi matched per ID + tanggal
    if ($nonfb) {
        $H = $nonfb['headers'];
        $cId = s10_find($H, ['ID trace', 'ID Rekaman', 'ID Trace']);
        $cWk = s10_find($H, ['waktu interaksi', 'Waktu Interaksi', 'timestamp']);
        foreach ($nonfb['rows'] as $rn => $c) {
            if ($rn === 1) continue;
            $id = $cId ? trim((string)($c[$cId] ?? '')) : '';
            if ($id === '') continue;
            $ensure($ID, $id); $ID[$id]['nonfb']++;
            if ($ID[$id]['date'] === '' && $cWk) { $d = s10_date($c[$cWk] ?? ''); if ($d !== '') $ID[$id]['date'] = $d; }
        }
    }

    // 2) Analisis Fallback -> UMK per ID + item TINDAK LANJUT (Intent Judgement terisi)
    if ($afb) {
        $H = $afb['headers'];
        $cId = s10_find($H, ['ID Percakapan', 'ID Rekaman', 'ID trace']);
        $cIns = s10_find($H, ['InsertId', 'InserId']);
        $cQ = s10_find($H, ['Pertanyaan User']);
        $cIntent = s10_find($H, ['Intent Judgement LLM']);
        $cTgl = s10_find($H, ['Tanggal Rekaman', 'Waktu Interaksi']);
        foreach ($afb['rows'] as $rn => $c) {
            if ($rn === 1) continue;
            $id = $cId ? trim((string)($c[$cId] ?? '')) : '';
            if ($id === '') continue;
            $ensure($ID, $id); $ID[$id]['umk']++;
            if ($ID[$id]['date'] === '' && $cTgl) { $d = s10_date($c[$cTgl] ?? ''); if ($d !== '') $ID[$id]['date'] = $d; }
            $intent = $cIntent ? trim((string)($c[$cIntent] ?? '')) : '';
            if ($intent !== '') {
                $ID[$id]['fb'][] = [
                    'pertanyaan' => $cQ ? (string)($c[$cQ] ?? '') : '',
                    'intent' => $intent,
                    'insertid' => $cIns ? (string)($c[$cIns] ?? '') : '',
                    'date' => $cTgl ? s10_date($c[$cTgl] ?? '') : '',
                ];
            }
        }
    }

    // 3) QA Conf MKTA -> peta komposit (Pertanyaan+Bot) => {id, insertid, date}
    $qaMap = [];
    if ($qa) {
        $H = $qa['headers'];
        $cId = s10_find($H, ['ID Rekaman', 'ID trace']);
        $cIns = s10_find($H, ['InsertId', 'InserId']);
        $cQ = s10_find($H, ['Pertanyaan User']);
        $cB = s10_find($H, ['Bot Response']);
        $cWk = s10_find($H, ['Waktu Interaksi']);
        foreach ($qa['rows'] as $rn => $c) {
            if ($rn === 1) continue;
            $key = (string)($c[$cQ] ?? '') . "\x1f" . (string)($c[$cB] ?? '');
            if (!isset($qaMap[$key])) {
                $qaMap[$key] = [
                    'id' => $cId ? trim((string)($c[$cId] ?? '')) : '',
                    'insertid' => $cIns ? (string)($c[$cIns] ?? '') : '',
                    'date' => $cWk ? s10_date($c[$cWk] ?? '') : '',
                ];
            }
        }
    }

    // 4) Analisis MKTA -> item MKTA TINDAK LANJUT (Intent Seharusnya terisi)
    if ($amkta) {
        $H = $amkta['headers'];
        $cQ = s10_find($H, ['Pertanyaan User']);
        $cB = s10_find($H, ['Bot Response']);
        $cSeh = s10_find($H, ['Intent Seharusnya']);
        foreach ($amkta['rows'] as $rn => $c) {
            if ($rn === 1) continue;
            $seh = $cSeh ? trim((string)($c[$cSeh] ?? '')) : '';
            if ($seh === '') continue;  // hanya TINDAK LANJUT
            $q = $cQ ? (string)($c[$cQ] ?? '') : '';
            $b = $cB ? (string)($c[$cB] ?? '') : '';
            $info = isset($qaMap[$q . "\x1f" . $b]) ? $qaMap[$q . "\x1f" . $b] : ['id' => '', 'insertid' => '', 'date' => ''];
            $id = $info['id'];
            if ($id === '') continue;
            $ensure($ID, $id); $ID[$id]['mkta']++;
            if ($ID[$id]['date'] === '' && $info['date'] !== '') $ID[$id]['date'] = $info['date'];
            $ID[$id]['mk'][] = ['pertanyaan' => $q, 'intent' => $seh, 'insertid' => $info['insertid'], 'date' => $info['date']];
        }
    }

    // ---- Bangun baris LM (hanya ID dengan minimal 1 TINDAK LANJUT) ----
    $today = date('Y-m-d');
    $penyusun = isset($_POST['penyusun']) ? trim((string)$_POST['penyusun']) : '';
    $lmHeader = ['TGL_REKAMAN', 'NOMOR_REKAMAN', 'NM_AGENT', 'HASIL_LM', 'CATATAN_LM'];
    $lmAoa = [$lmHeader];
    $pembHeader = ['INSERT_ID', 'NAMA_MATERI', 'TGL PENYUSUNAN', 'NAMA PENYUSUN', 'RANGKUMAN', 'STATUS MATERI', 'KATEGORI'];
    $pembAoa = [$pembHeader];

    $ids = array_keys($ID);
    // urutkan berdasarkan tanggal lalu ID
    usort($ids, function ($a, $b) use ($ID) {
        $c = strcmp($ID[$a]['date'], $ID[$b]['date']);
        return $c !== 0 ? $c : strcmp($a, $b);
    });

    foreach ($ids as $id) {
        $d = $ID[$id];
        $hasTL = (count($d['fb']) > 0) || (count($d['mk']) > 0);
        if (!$hasTL) continue;
        $mkta = $d['mkta'];
        $mka = max(0, $d['nonfb'] - $mkta);
        $umk = $d['umk'];

        $matchedNotes = [];
        foreach ($d['mk'] as $it) {
            $matchedNotes[] = "Bot tidak akurat dalam merespon query user. Menambahkan '" . $it['pertanyaan'] . "' sebagai training phrase intent '" . $it['intent'] . "'";
        }
        $unmatchedNotes = [];
        foreach ($d['fb'] as $it) {
            $unmatchedNotes[] = "Menambahkan frasa '" . $it['pertanyaan'] . "' sebagai training phrase intent " . $it['intent'];
        }
        $catatan = "Matched Kontent Akurat: " . $mka . " \n" .
                   "Matched Kontent Tidak Akurat: " . $mkta . " \n" .
                   "Unmatched Kontent: " . $umk . " \n" .
                   "Catatan Matched Kontent: " . (count($matchedNotes) ? implode("\n", $matchedNotes) : "-") . " \n" .
                   "Catatan Unmatched Kontent: " . (count($unmatchedNotes) ? implode("\n", $unmatchedNotes) : "0");
        $lmAoa[] = [$d['date'], $id, 'CHATBOT', 'TINDAK LANJUT', $catatan];

        // Pembaruan: 1 baris per item TL (fallback lalu mkta)
        foreach ($d['fb'] as $it) {
            $rang = "Dasar Pembaruan (No Rekaman): " . $id . " \nPerubahan: Menambahkan frasa '" . $it['pertanyaan'] . "' sebagai training phrase intent " . $it['intent'];
            $tglp = ($it['date'] !== '') ? $it['date'] : (($d['date'] !== '') ? $d['date'] : $today);
            $pembAoa[] = [$it['insertid'], $it['intent'], $tglp, $penyusun, $rang, 'Pembaruan Materi LM', 'Fallback'];
        }
        foreach ($d['mk'] as $it) {
            $rang = "Dasar Pembaruan (No Rekaman): " . $id . " \nPerubahan: Menambahkan frasa '" . $it['pertanyaan'] . "' sebagai training phrase intent " . $it['intent'];
            $tglp = ($it['date'] !== '') ? $it['date'] : (($d['date'] !== '') ? $d['date'] : $today);
            $pembAoa[] = [$it['insertid'], $it['intent'], $tglp, $penyusun, $rang, 'Pembaruan Materi LM', 'MKTA'];
        }
    }

    if (count($lmAoa) === 1) throw new Exception('Tidak ada baris TINDAK LANJUT (Fallback/MKTA) untuk dilaporkan. Isi dulu Step 6 / Step 9.');

    // ---- Excel Utama: tambah sheet LM & Pembaruan ----
    $excel = xlsx_upsert_sheet($srcPath, 'LM', $lmAoa);
    $tmp = tempnam(sys_get_temp_dir(), 's10x') . '.xlsx';
    file_put_contents($tmp, $excel);
    $excel = xlsx_upsert_sheet($tmp, 'Pembaruan', $pembAoa);
    @unlink($tmp);

    // ---- CSV ----
    $csvLm = s10_csv($lmAoa);  // KATEGORI memang tidak ada di LM
    // CSV Pembaruan: buang kolom INSERT_ID (idx 0) & KATEGORI (idx terakhir)
    $pembCsvAoa = [];
    foreach ($pembAoa as $row) {
        $pembCsvAoa[] = array_slice($row, 1, 5);  // NAMA_MATERI..STATUS MATERI
    }
    $csvPemb = s10_csv($pembCsvAoa);

    // simpan
    file_put_contents(run_dir($cfg) . '/step10_lm.csv', $csvLm);
    file_put_contents(run_dir($cfg) . '/step10_pembaruan.csv', $csvPemb);
    $summary = [
        'status' => 'Selesai',
        'baris_LM' => count($lmAoa) - 1,
        'baris_Pembaruan' => count($pembAoa) - 1,
    ];
    $data = save_artifact($cfg, 10, 'xlsx', $excel, 'Laporan_Utama.xlsx', $summary);
    return [
        'step' => 10, 'artifact' => $data,
        'lm_rows' => count($lmAoa) - 1,
        'pembaruan_rows' => count($pembAoa) - 1,
    ];
}

/*==================================================================
| Helper: POST multipart lalu terima balasan JSON (bukan file biner).
| Dipakai Step 11 karena backend membalas {ok, report, stats, zip_b64}.
*=================================================================*/
function curl_post_json($endpoint, $apiKey, $files, $fields = []) {
    $post = [];
    $tmps = [];
    foreach ($files as $field => $info) {
        $t = tempnam(sys_get_temp_dir(), 'up');
        file_put_contents($t, $info[0]);
        $tmps[] = $t;
        $mime = isset($info[2]) ? $info[2] : 'application/octet-stream';
        $post[$field] = new CURLFile($t, $mime, $info[1]);
    }
    foreach ($fields as $k => $v) { $post[$k] = $v; }
    $ch = curl_init($endpoint);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => $post,
        CURLOPT_HTTPHEADER => ['X-API-Key: ' . $apiKey, 'ngrok-skip-browser-warning: true'],
        CURLOPT_TIMEOUT => 3600,
    ]);
    $res = curl_exec($ch);
    $status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err = curl_error($ch);
    curl_close($ch);
    foreach ($tmps as $t) { @unlink($t); }
    if ($res === false) throw new Exception('Gagal menghubungi server Colab: ' . $err);
    if ($status < 200 || $status >= 300) throw new Exception('Server error (HTTP ' . $status . '): ' . substr((string)$res, 0, 300));
    $data = json_decode($res, true);
    if (!is_array($data)) {
        $peek = trim(preg_replace('/\s+/', ' ', strip_tags((string)$res)));
        throw new Exception('Server tidak mengembalikan JSON valid (mungkin halaman error/interstitial). Cuplikan: ' . substr($peek, 0, 300));
    }
    return $data;
}

/*==================================================================
| STEP 11 - Pembaruan Intent Dialogflow.
|  Menurunkan daftar training phrase (id/tp/lang) dari sheet Analisis
|  Fallback (Intent Judgement LLM) + Analisis MKTA (Intent Seharusnya)
|  pada workbook (hasil step sebelumnya ATAU upload), lalu mengirim ZIP
|  export Dialogflow + daftar frasa ke backend Python (/api/update-usersays).
|  Output: workbook + sheet "Status Pembaruan" (artifact utama) dan ZIP
|  usersays terbaru (part=zip11).
*=================================================================*/
function s11_derive_phrases($workbookBytes, $lang) {
    $tmp = tempnam(sys_get_temp_dir(), 's11wb') . '.xlsx';
    file_put_contents($tmp, $workbookBytes);
    $items = [];
    try {
        $zip = xlsx_zip_open($tmp);
        $shared = xlsx_shared_strings($zip);
        $sf = xlsx_sheet_files($zip);
        $read = function ($sheetName) use ($zip, $sf, $shared) {
            if (!isset($sf[$sheetName])) return null;
            return xlsx_read_sheet($zip, $sf[$sheetName], $shared);
        };
        $afb = $read('Analisis Fallback');
        $amkta = $read('Analisis MKTA');
        $zip->close();
        if ($afb) {
            $H = $afb['headers'];
            $cQ = s10_find($H, ['Pertanyaan User']);
            $cIntent = s10_find($H, ['Intent Judgement LLM']);
            foreach ($afb['rows'] as $rn => $c) {
                if ($rn === 1) continue;
                $intent = $cIntent ? trim((string)($c[$cIntent] ?? '')) : '';
                $q = $cQ ? trim((string)($c[$cQ] ?? '')) : '';
                if ($intent === '' || $q === '') continue;
                $items[] = ['id' => $intent, 'tp' => $q, 'lang' => $lang];
            }
        }
        if ($amkta) {
            $H = $amkta['headers'];
            $cQ = s10_find($H, ['Pertanyaan User']);
            $cSeh = s10_find($H, ['Intent Seharusnya']);
            foreach ($amkta['rows'] as $rn => $c) {
                if ($rn === 1) continue;
                $seh = $cSeh ? trim((string)($c[$cSeh] ?? '')) : '';
                $q = $cQ ? trim((string)($c[$cQ] ?? '')) : '';
                if ($seh === '' || $q === '') continue;
                $items[] = ['id' => $seh, 'tp' => $q, 'lang' => $lang];
            }
        }
    } finally {
        @unlink($tmp);
    }
    return $items;
}

function step11_update($cfg) {
    $rawBase = isset($_POST['ngrok_url']) ? trim($_POST['ngrok_url']) : '';
    $endpoint = api_endpoint($cfg, $rawBase, '/api/update-usersays');
    if (empty($cfg['force_local_api']) && $rawBase !== '') save_ngrok($cfg, $rawBase);

    $lang = strtolower(trim(isset($_POST['lang']) ? $_POST['lang'] : 'id'));
    if (!in_array($lang, ['id', 'en'], true)) throw new Exception('Bahasa harus id atau en.');
    $mode = isset($_POST['mode']) ? $_POST['mode'] : 'auto';

    // ZIP export Dialogflow (wajib pada kedua opsi).
    list($zipBytes, $zipName) = read_upload('df_zip', ['zip'], 'ZIP export Dialogflow');

    // Workbook sumber (untuk menurunkan daftar frasa + tempat sheet baru).
    if ($mode === 'manual') {
        list($wbBytes, $wbName) = read_upload('workbook', ['xlsx'], 'Workbook pipeline (punya sheet Analisis Fallback/MKTA)');
    } else {
        $state = load_state($cfg);
        $pick = null;
        foreach (['10', '9'] as $s) {
            $st = isset($state['steps'][$s]) ? $state['steps'][$s] : null;
            if ($st && !empty($st['file']) && strtolower($st['ext']) === 'xlsx') { $pick = $st; break; }
        }
        if (!$pick) throw new Exception('Belum ada hasil ber-sheet "Analisis MKTA". Jalankan Step 9 (idealnya sampai Step 10), atau pakai opsi Upload workbook.');
        $p = run_dir($cfg) . '/' . $pick['file'];
        if (!is_file($p)) throw new Exception('File workbook sumber hilang dari server.');
        $wbBytes = file_get_contents($p);
        $wbName = $pick['name'];
    }

    // Turunkan daftar frasa (id/tp/lang) dari sheet Analisis.
    $phrases = s11_derive_phrases($wbBytes, $lang);
    if (!count($phrases)) throw new Exception('Tidak ada training phrase TINDAK LANJUT (Analisis Fallback/MKTA) untuk diproses.');

    // Kirim ZIP + daftar frasa ke backend Python.
    $resp = curl_post_json($endpoint, $cfg['qwen_api_key'], [
        'zip_file' => [$zipBytes, $zipName ?: 'agent.zip', 'application/zip'],
    ], [
        'phrases' => json_encode(array_values($phrases), JSON_UNESCAPED_UNICODE),
    ]);

    if (empty($resp['ok'])) throw new Exception('Backend gagal memproses pembaruan usersays.');
    $report = (isset($resp['report']) && is_array($resp['report'])) ? $resp['report'] : [];
    $stats = (isset($resp['stats']) && is_array($resp['stats'])) ? $resp['stats'] : [];
    $zipB64 = isset($resp['zip_b64']) ? $resp['zip_b64'] : '';
    $zipOut = base64_decode($zipB64);
    if ($zipOut === false || strlen($zipOut) < 4 || substr($zipOut, 0, 2) !== 'PK') {
        throw new Exception('ZIP hasil dari backend tidak valid.');
    }

    // Simpan ZIP usersays terbaru (diunduh via part=zip11).
    file_put_contents(run_dir($cfg) . '/step11_usersays.zip', $zipOut);

    // Tambahkan sheet "Status Pembaruan" ke workbook yang sudah ada.
    $aoa = [['Intent ID', 'Language', 'Training Phrase', 'Keterangan', 'File Target', 'Status']];
    foreach ($report as $r) {
        $aoa[] = [
            (string)($r[0] ?? ''), (string)($r[1] ?? ''), (string)($r[2] ?? ''),
            (string)($r[3] ?? ''), (string)($r[4] ?? ''), (string)($r[5] ?? ''),
        ];
    }
    $tmp = tempnam(sys_get_temp_dir(), 's11up') . '.xlsx';
    file_put_contents($tmp, $wbBytes);
    $excel = xlsx_upsert_sheet($tmp, 'Status Pembaruan', $aoa);
    @unlink($tmp);

    $summary = [
        'status' => 'Selesai',
        'bahasa' => $lang,
        'total_frasa' => isset($stats['total']) ? $stats['total'] : count($report),
        'ditambahkan' => isset($stats['ditambahkan']) ? $stats['ditambahkan'] : 0,
        'duplikat' => isset($stats['duplikat']) ? $stats['duplikat'] : 0,
        'intent_tidak_ketemu' => isset($stats['tidak_ketemu']) ? $stats['tidak_ketemu'] : 0,
        'file_diperbarui' => isset($stats['file_diperbarui']) ? $stats['file_diperbarui'] : 0,
        'endpoint' => $endpoint,
    ];
    $data = save_artifact($cfg, 11, 'xlsx', $excel, 'Laporan_Pembaruan_Intent.xlsx', $summary);
    return ['step' => 11, 'artifact' => $data, 'stats' => $summary];
}

/*==================================================================
| HALAMAN UI
*=================================================================*/
function render_page() {
    header('Content-Type: text/html; charset=utf-8');
    echo page_html();
}

function page_html() {
    return <<<'PAGE'
<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dialogflow Pipeline</title>
<style>
:root{
  --text:#2C2C2B; --text2:#7D7A75; --canvas:#FFFFFF; --soft:#F9F8F7;
  --soft2:#F0EFED; --border:#E6E5E3; --blue:#2783DE; --blue-soft:#E5F2FC;
  --green:#46A171; --green-soft:#E8F1EC; --orange:#D5803B; --orange-soft:#FBEBDE;
  --red:#E56458; --red-soft:#FCE9E7;
  --shadow:0 1px 2px rgba(0,0,0,.05),0 8px 24px rgba(0,0,0,.06);
  --radius:12px;
}
@media (prefers-color-scheme: dark){
  :root{
    --text:#FFFFFF; --text2:rgba(255,255,255,.65); --canvas:#191919; --soft:#202020;
    --soft2:#262626; --border:rgba(255,255,255,.14); --blue:#5E9FE8; --blue-soft:rgba(94,159,232,.14);
    --green:#72BC8F; --green-soft:rgba(114,188,143,.14); --orange:#DE9255; --orange-soft:rgba(222,146,85,.14);
    --red:#E97366; --red-soft:rgba(233,115,102,.14);
    --shadow:0 1px 2px rgba(0,0,0,.4),0 12px 32px rgba(0,0,0,.45);
  }
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  background:var(--canvas); color:var(--text); font-size:16px; line-height:1.5;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1080px; margin:0 auto; padding:32px 24px 80px}

/* Header */
.top{display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; margin-bottom:8px}
.brand{display:flex; align-items:center; gap:14px}
.logo{width:44px; height:44px; border-radius:12px; background:var(--blue); display:flex; align-items:center; justify-content:center; color:#fff; flex:none}
.logo svg{width:24px; height:24px}
.brand h1{font-size:22px; margin:0; letter-spacing:-.01em}
.brand p{margin:2px 0 0; color:var(--text2); font-size:14px}
.runchip{display:flex; align-items:center; gap:10px}
.chip{background:var(--soft2); border:1px solid var(--border); border-radius:999px; padding:6px 12px; font-size:12.5px; color:var(--text2); font-family:ui-monospace,Menlo,Consolas,monospace}
.btn-ghost{background:transparent; border:1px solid var(--border); color:var(--text); padding:8px 14px; border-radius:8px; font-size:13.5px; cursor:pointer; transition:.15s}
.btn-ghost:hover{background:var(--soft2)}

.intro{background:var(--soft); border:1px solid var(--border); border-radius:var(--radius); padding:16px 18px; margin:20px 0 28px; color:var(--text2); font-size:14px; display:flex; gap:12px; align-items:flex-start}
.intro svg{width:20px;height:20px;flex:none;color:var(--blue);margin-top:1px}

/* Stepper rail */
.railwrap{overflow-x:auto; padding:12px 4px 8px; -webkit-overflow-scrolling:touch}
.rail{display:flex; align-items:flex-start; min-width:max-content; padding:8px 0}
.node{display:flex; flex-direction:column; align-items:center; width:132px; flex:none; cursor:pointer; text-align:center; background:none; border:none; font-family:inherit; padding:0}
.dot{width:52px; height:52px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:18px; font-weight:600;
  background:var(--soft2); color:var(--text2); border:2px solid var(--border); transition:.18s; position:relative}
.dot svg{width:24px;height:24px}
.node:hover .dot{transform:translateY(-2px); box-shadow:var(--shadow)}
.node .lbl{margin-top:10px; font-size:13px; font-weight:600; color:var(--text); line-height:1.25}
.node .sub{margin-top:2px; font-size:11.5px; color:var(--text2); line-height:1.2; padding:0 6px}
.connector{flex:none; width:40px; height:2px; background:var(--border); margin-top:26px; border-radius:2px}

.node.is-ready .dot{background:var(--blue-soft); color:var(--blue); border-color:var(--blue)}
.node.is-active .dot{background:var(--blue); color:#fff; border-color:var(--blue); box-shadow:0 0 0 4px var(--blue-soft)}
.node.is-done .dot{background:var(--green); color:#fff; border-color:var(--green)}
.node.is-error .dot{background:var(--red); color:#fff; border-color:var(--red)}
.node.is-soon .dot{opacity:.55; border-style:dashed}
.connector.done{background:var(--green)}

.legend{display:flex; gap:18px; flex-wrap:wrap; margin-top:18px; color:var(--text2); font-size:12.5px}
.legend span{display:flex; align-items:center; gap:7px}
.ld{width:12px; height:12px; border-radius:50%; display:inline-block}

/* Modal */
.overlay{position:fixed; inset:0; background:rgba(20,20,20,.55); backdrop-filter:blur(3px); display:none; align-items:flex-start; justify-content:center; padding:40px 16px; z-index:50; overflow-y:auto}
.overlay.show{display:flex}
.modal{background:var(--canvas); width:100%; max-width:560px; border-radius:16px; border:1px solid var(--border); box-shadow:var(--shadow); overflow:hidden; animation:pop .18s ease}
.modal.wide{max-width:1120px}
.s6bar{display:flex; gap:12px; flex-wrap:wrap; align-items:flex-end; padding:16px 24px 12px; border-bottom:1px solid var(--border)}
.s6bar .fg{display:flex; flex-direction:column; gap:5px}
.s6bar label{font-size:11.5px; font-weight:600; color:var(--text2)}
.s6bar select,.s6bar input{padding:7px 10px; border:1px solid var(--border); border-radius:8px; background:var(--soft); color:var(--text); font-size:13px; font-family:inherit}
.s6bar .count{margin-left:auto; font-size:12.5px; color:var(--text2); align-self:center}
.s6wrap{max-height:60vh; overflow:auto; padding:0 12px}
.s6table{width:100%; border-collapse:collapse; font-size:13px}
.s6table th{position:sticky; top:0; background:var(--soft2); text-align:left; padding:9px 10px; font-size:11.5px; color:var(--text2); border-bottom:1px solid var(--border); z-index:1}
.s6table td{padding:8px 10px; border-bottom:1px solid var(--border); vertical-align:top}
.s6table tr:hover td{background:var(--soft)}
.s6q{max-width:280px; white-space:pre-wrap; word-break:break-word}
.s6isi{max-width:300px; max-height:96px; overflow:auto; white-space:pre-wrap; word-break:break-word; color:var(--text2); font-size:12px}
.s6intent{flex:1; min-width:150px; padding:6px 8px; border:1px solid var(--border); border-radius:7px; background:var(--canvas); color:var(--text); font-size:12.5px; font-family:inherit}
.s6intent.edited{border-color:var(--blue); box-shadow:0 0 0 2px var(--blue-soft)}
.s6combo{position:relative; display:flex; align-items:center; gap:4px; min-width:250px}
.s6arrow{border:1px solid var(--border); background:var(--soft2); color:var(--text2); width:28px; height:32px; border-radius:7px; cursor:pointer; font-size:12px; flex:none; line-height:1}
.s6arrow:hover{background:var(--blue-soft); color:var(--blue); border-color:var(--blue)}
.s6menu{display:none; position:fixed; z-index:60; background:var(--canvas); border:1px solid var(--border); border-radius:10px; box-shadow:var(--shadow); max-height:280px; overflow:auto; min-width:260px}
.s6menu.open{display:block}
.s6opt{padding:8px 12px; cursor:pointer; border-bottom:1px solid var(--border)}
.s6opt:last-child{border-bottom:none}
.s6opt:hover{background:var(--blue-soft)}
.s6opt-id{font-size:12.5px; color:var(--text); font-weight:600; word-break:break-word}
.s6opt-meta{font-size:11px; color:var(--text2); margin-top:2px}
.s6empty{color:var(--text2); cursor:default}
.s6pill{display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:600}
.s6pill.t{background:var(--green-soft); color:var(--green)}
.s6pill.m{background:var(--orange-soft); color:var(--orange)}
.s6pill.n{background:var(--soft2); color:var(--text2)}
@keyframes pop{from{opacity:0; transform:translateY(8px) scale(.98)} to{opacity:1; transform:none}}
.mhead{display:flex; align-items:flex-start; gap:14px; padding:22px 24px 16px; border-bottom:1px solid var(--border)}
.mbadge{width:40px; height:40px; border-radius:10px; background:var(--blue-soft); color:var(--blue); display:flex; align-items:center; justify-content:center; font-weight:700; flex:none}
.mhead h2{margin:0; font-size:17px; letter-spacing:-.01em}
.mhead p{margin:3px 0 0; color:var(--text2); font-size:13px}
.mx{margin-left:auto; background:var(--soft2); border:1px solid var(--border); width:34px; height:34px; border-radius:8px; cursor:pointer; color:var(--text); font-size:18px; line-height:1; display:flex; align-items:center; justify-content:center; flex:none; transition:.15s}
.mx:hover{background:var(--red-soft); color:var(--red); border-color:var(--red)}
.mbody{padding:20px 24px 8px}
.field{margin-bottom:16px}
.field label{display:block; font-size:13px; font-weight:600; margin-bottom:6px}
.field .hint{font-size:12px; color:var(--text2); margin-top:5px}
input[type=text],input[type=date],input[type=file],select,textarea{
  width:100%; padding:10px 12px; border:1px solid var(--border); border-radius:8px; background:var(--soft);
  color:var(--text); font-size:14px; font-family:inherit; transition:.15s}
input:focus,select:focus,textarea:focus{outline:none; border-color:var(--blue); box-shadow:0 0 0 3px var(--blue-soft)}
input[type=file]{padding:8px}
.srcbox{display:flex; gap:8px; background:var(--soft2); border-radius:10px; padding:4px; margin-bottom:14px}
.srcbox button{flex:1; border:none; background:transparent; padding:9px; border-radius:7px; font-size:13px; font-weight:600; color:var(--text2); cursor:pointer; font-family:inherit}
.srcbox button.on{background:var(--canvas); color:var(--blue); box-shadow:var(--shadow)}
.mfoot{padding:8px 24px 22px; display:flex; gap:10px; align-items:center; flex-wrap:wrap}
.btn{background:var(--blue); color:#fff; border:none; padding:11px 20px; border-radius:9px; font-size:14px; font-weight:600; cursor:pointer; transition:.15s; font-family:inherit}
.btn:hover{filter:brightness(1.05)}
.btn:disabled{opacity:.6; cursor:not-allowed}
.btn-sec{background:var(--soft2); color:var(--text); border:1px solid var(--border)}
.btn-ok{background:var(--green)}

.status{margin:4px 24px 0; padding:14px 16px; border-radius:10px; font-size:13.5px; display:none}
.status.show{display:block}
.status.run{background:var(--blue-soft); color:var(--blue)}
.status.err{background:var(--red-soft); color:var(--red)}
.status.ok{background:var(--green-soft); color:var(--green)}
.status .sp{display:inline-block; width:15px; height:15px; border:2px solid currentColor; border-right-color:transparent; border-radius:50%; animation:spin .7s linear infinite; vertical-align:-2px; margin-right:8px}
@keyframes spin{to{transform:rotate(360deg)}}
.summary{margin:12px 24px 0; border:1px solid var(--border); border-radius:10px; overflow:hidden; font-size:13px; display:none}
.summary.show{display:block}
.summary .row{display:flex; justify-content:space-between; gap:12px; padding:8px 14px; border-bottom:1px solid var(--border)}
.summary .row:last-child{border-bottom:none}
.summary .row .k{color:var(--text2)}
.summary .row .v{font-weight:600; text-align:right; word-break:break-word}
.soon{padding:26px 24px 30px; text-align:center; color:var(--text2)}
.soon svg{width:40px;height:40px;color:var(--text2);opacity:.6;margin-bottom:10px}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div class="brand">
      <div class="logo"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16M4 12h16M4 18h10"/></svg></div>
      <div>
        <h1>Dialogflow Pipeline</h1>
        <p>Jalankan step 1 sampai 10 secara berurutan &mdash; pengganti n8n</p>
      </div>
    </div>
    <div class="runchip">
      <span class="chip" id="runLabel">run: &mdash;</span>
      <button class="btn-ghost" id="resetBtn">Reset Run</button>
    </div>
  </div>

  <div class="intro">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
    <div>Klik lingkaran step untuk membuka formnya. Setiap step berdiri sendiri: kalau ada yang gagal, step lain yang sudah sukses tidak hilang. Setelah sebuah step selesai, step berikutnya bisa langsung memakai hasilnya tanpa upload ulang.</div>
  </div>

<div id="flows-container"></div>

  <div class="legend">
    <span><i class="ld" style="background:var(--blue)"></i> Siap dijalankan</span>
    <span><i class="ld" style="background:var(--green)"></i> Selesai</span>
    <span><i class="ld" style="background:var(--red)"></i> Error</span>
    <span><i class="ld" style="background:var(--soft2);border:1px dashed var(--border)"></i> Belum tersedia</span>
  </div>
</div>

<div class="overlay" id="overlay">
  <div class="modal" id="modal"></div>
</div>

<script>
const ENDPOINT = location.pathname;
const STEPS = [
  {n:1, title:'Tarik Data Dialogflow', sub:'Raw JSON dari Google Logging', type:'form1'},
  {n:2, title:'Convert JSON \u2192 XLSX', sub:'Multi-sheet interaksi', type:'form2'},
  {n:3, title:'Training & Intent', sub:'2 XLSX dalam ZIP', type:'form3'},
  {n:4, title:'Analisis Rekomendasi', sub:'SBERT+BGE Top 5 via Ngrok', type:'form4'},
  {n:5, title:'Qwen Judgement Top 5', sub:'Skor 0\u20135 via Ngrok', type:'form5'},
  {n:6, title:'Cross-check Manual', sub:'Koreksi manusia (Analisis Fallback)', type:'step6'},
  {n:7, title:'Analisis MKTA', sub:'Relevansi jawaban (Non Fallback)', type:'form7'},
  {n:8, title:'Putusan LLM MKTA', sub:'Filter QA Conf \u2192 Qwen', type:'step8'},
  {n:9, title:'Analisis Manual MKTA', sub:'Isi Intent Seharusnya', type:'step9'},
  {n:10, title:'Laporan LM & Pembaruan', sub:'Excel + CSV LM + CSV Pembaruan', type:'step10'},
  {n:11, title:'Pembaruan Intent Dialogflow', sub:'Suntik training phrase ke usersays JSON', type:'step11'},
  {n:12, title:'Avaya - Upload JSON', sub:'Gabung transkrip AWE Avaya', type:'avaya12'},
  {n:13, title:'Avaya - Tarik Intent Dialogflow', sub:'Sama seperti Step 3', type:'avaya13'},
  {n:14, title:'Avaya - Analisis', sub:'Coverage, deflection, sentimen (JSON)', type:'avaya14'},
  {n:15, title:'Avaya - Dashboard', sub:'Render HTML interaktif', type:'avaya15'},
  {n:16, title:'Avaya - Ekspor Excel', sub:'Workbook multi-sheet', type:'avaya16'},
];

let RUN = localStorage.getItem('dfp_run');
if(!RUN){ RUN = 'run_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2,7); localStorage.setItem('dfp_run', RUN); }
let STATE = {steps:{}};

const checkSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>';

function api(action, opts){
  const u = ENDPOINT + '?action=' + action + '&run=' + encodeURIComponent(RUN);
  return fetch(u, opts||{}).then(async r=>{
    const text = await r.text();
    try { return JSON.parse(text); }
    catch(e){
      const snip = text.replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').trim().slice(0,200);
      const looksHtml = text.trim().charAt(0)==='<';
      const hint = (looksHtml || r.status>=500 || r.status===0)
        ? 'Server balas halaman error (HTTP '+r.status+') \u2014 kemungkinan proses terlalu lama / gateway timeout (ngrok). Untuk Step 8 proses sudah per-chunk; klik lagi untuk melanjutkan progres.'
        : 'Respons server tidak valid (HTTP '+r.status+').';
      throw new Error(hint + (snip ? ' Cuplikan: '+snip : ''));
    }
  });
}

// Polling status step: dipakai bila request gagal karena gateway timeout (ngrok
// 503) padahal proses SERVER kemungkinan masih jalan & tetap menyimpan hasil.
function pollStepDone(n, onDone, onFail){
  let tries=0; const max=360; // ~30 menit @5s
  const iv=setInterval(()=>{
    tries++;
    api('state',{}).then(res=>{
      if(res && res.steps){ STATE.steps=res.steps; if(res.ngrok_url) STATE.ngrok_url=res.ngrok_url; }
      const st=STATE.steps[n];
      if(st && st.status==='done'){ clearInterval(iv); onDone(st); }
      else if(st && st.status==='error'){ clearInterval(iv); onFail('server melaporkan error'); }
      else if(tries>=max){ clearInterval(iv); onFail('waktu tunggu habis'); }
    }).catch(()=>{ if(tries>=max){ clearInterval(iv); onFail('waktu tunggu habis'); } });
  }, 5000);
}

function stepStatus(n){
  const s = STATE.steps[n];
  if(s && s.status==='done') return 'done';
  if(s && s.status==='error') return 'error';
  const meta = STEPS.find(x=>x.n===n);
  if(meta.type==='soon') return 'soon';
  // ready jika step 1, atau step sebelumnya sudah done
  if(n===1 || n===12 || n===13) return 'ready';
  if(n===14){ const a=STATE.steps[12]; const b=STATE.steps[13]||STATE.steps[3]; return (a&&a.status==='done'&&b&&b.status==='done')?'ready':'pending'; }
  if(n===15||n===16){ const a=STATE.steps[14]; return (a&&a.status==='done')?'ready':'pending'; }
  const prev = STATE.steps[n-1];
  return (prev && prev.status==='done') ? 'ready' : 'pending';
}

function renderRail(){
  const container = document.getElementById('flows-container');
  container.innerHTML = ''; // bersihkan isi sebelumnya
  
  // Ubah nama judul sesuai permintaan Anda
  const flows = [
    {title:'Analisis DialogFlow (Step 1-11)', diag:false, steps:STEPS.filter(s=>s.n<=11)},
    {title:'Analisis AWE (Step 12-16)', diag:true, steps:STEPS.filter(s=>s.n>=12)},
  ];
  
  flows.forEach((fl,fi)=>{
    // 1. Buat Judul (Header) untuk masing-masing baris
    const head = document.createElement('div');
    head.style.cssText = 'margin:'+(fi===0?'0':'32px')+' 0 12px;padding-top:'+(fi===0?'0':'24px')+';font-size:13px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:var(--text2);'+(fi===0?'':'border-top:1px dashed var(--border);');
    head.innerHTML = '<span>'+esc(fl.title)+'</span>' + (fl.diag ? ' <button type="button" id="diagBtn" style="margin-left:12px;font-size:11px;font-weight:700;padding:4px 10px;border-radius:8px;border:1px solid var(--border);background:var(--soft2);color:var(--text2);cursor:pointer">\uD83E\uDE7A Cek Server</button>' : '');
    container.appendChild(head);
    
    // 2. Buat wrapper SCROLL khusus untuk baris ini
    const wrap = document.createElement('div');
    wrap.className = 'railwrap';
    // Hapus padding atas/bawah bawaan CSS agar jaraknya lebih pas dengan judul
    wrap.style.paddingTop = '4px'; 
    
    // 3. Buat container urutan Step
    const rail = document.createElement('div');
    rail.className = 'rail';
    
    fl.steps.forEach((st,i)=>{
      if(i>0){
        const c = document.createElement('div');
        c.className = 'connector' + (stepStatus(st.n)==='done' && stepStatus(fl.steps[i-1].n)==='done' ? ' done':'');
        rail.appendChild(c);
      }
      const stat = stepStatus(st.n);
      const node = document.createElement('button');
      node.className = 'node' + (stat==='done'?' is-done':stat==='error'?' is-error':stat==='ready'?' is-ready':st.type==='soon'?' is-soon':'');
      node.innerHTML =
        '<span class="dot">' + (stat==='done'? checkSvg : st.n) + '</span>' +
        '<span class="lbl">' + st.title + '</span>' +
        '<span class="sub">' + st.sub + '</span>';
      node.onclick = ()=>openModal(st.n);
      rail.appendChild(node);
    });
    
    // 4. Masukkan susunan step ke dalam wrapper scroll, lalu masukkan ke container utama
    wrap.appendChild(rail);
    container.appendChild(wrap);
  });
  
  const db = document.getElementById('diagBtn'); if(db) db.onclick = checkServer;
  document.getElementById('runLabel').textContent = 'run: ' + RUN;
}

// Diagnostik server: tampilkan build, template, dependensi. Membantu memastikan
// runtime Colab memakai kode terbaru (bila build kosong/404 = belum di-restart).
function checkServer(){
  const modal = document.getElementById('modal');
  modal.classList.remove('wide');
  modal.innerHTML =
    '<div class="mhead"><div class="mbadge">?</div>'+
      '<div><h2>Diagnostik Server</h2><p>Cek koneksi, versi modul &amp; template Avaya</p></div>'+
      '<button class="mx" id="mxBtn" title="Tutup">&times;</button></div>'+
    '<div class="mbody" id="mbody"><div class="status show run"><span class="sp"></span>Menghubungi server...</div></div>'+
    '<div class="mfoot"><button class="btn btn-sec" id="closeBtn2">Tutup</button></div>';
  document.getElementById('overlay').classList.add('show');
  document.getElementById('mxBtn').onclick = closeModal;
  document.getElementById('closeBtn2').onclick = closeModal;
  const extra = STATE.ngrok_url ? '&ngrok_url='+encodeURIComponent(STATE.ngrok_url) : '';
  api('avayadiag'+extra, {}).then(res=>{
    const d = (res && res.diag) ? res.diag : res;
    let rows='';
    for(const k in d){
      if(k==='ok') continue;
      let v = d[k]; if(typeof v==='object') v=JSON.stringify(v);
      rows+='<div class="row"><span class="k">'+esc(k.replace(/_/g," "))+'</span><span class="v">'+esc(v)+'</span></div>';
    }
    document.getElementById('mbody').innerHTML='<div class="summary show">'+rows+'</div>';
  }).catch(e=>{
    document.getElementById('mbody').innerHTML='<div class="status show err">\u26A0 '+esc(e.message||e)+'</div>';
  });
}

function esc(s){ return String(s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

// Toggle sumber input generik. opts: [{mode,label,enabled}]. Default = opsi enabled pertama.
function srcToggle(group, opts){
  let html='<div class="srcbox" data-group="'+group+'">';
  let firstEnabledDone=false;
  opts.forEach(o=>{
    const enabled = o.enabled!==false;
    let on='';
    if(enabled && !firstEnabledDone){ on=' on'; firstEnabledDone=true; }
    const dis = enabled ? '' : ' disabled style="opacity:.45;cursor:not-allowed"';
    html+='<button type="button" class="srcbtn'+on+'" data-group="'+group+'" data-mode="'+o.mode+'"'+dis+'>'+o.label+'</button>';
  });
  html+='</div>';
  return html;
}

function formHtml(n){
  if(n===1){
    return ''+
     '<div class="field"><label>Start Date</label><input type="date" id="f_start"></div>'+
     '<div class="field"><label>End Date</label><input type="date" id="f_end"><div class="hint">Rentang maksimal 31 hari, dan harus sebelum hari ini (WIB).</div></div>'+
     '<div class="field"><label>Bahasa</label><select id="f_lang"><option value="id">id</option><option value="en">en</option></select></div>'+
     '<div class="field"><label>Access Token Google <span style="font-weight:400;color:var(--text2)">(opsional)</span></label><input type="text" id="f_token" placeholder="Kosongkan bila pakai service-account.json"><div class="hint">Isi bila server tidak memakai service-account.json.</div></div>';
  }
  if(n===2){
    const d1 = STATE.steps[1] && STATE.steps[1].status==='done';
    return srcToggle('s2',[{mode:'prev',label:'Hasil Step 1',enabled:d1},{mode:'upload',label:'Unggah File'}])+
     '<div class="field srcfield" data-group="s2" data-mode="prev"><div class="hint">Memakai hasil <b>Step 1</b> (raw log) dari server. Tidak perlu unggah ulang.</div></div>'+
     '<div class="field srcfield" data-group="s2" data-mode="upload"><label>File JSON</label><input type="file" id="f_json" accept=".json,application/json"><div class="hint">Hasil tarikan Step 1 (raw log Dialogflow).</div></div>';
  }
  if(n===3){
    return '<div class="hint" style="margin-bottom:14px">Menarik seluruh intent aktif dari Dialogflow lalu membuat 2 file XLSX (Training Phrase &amp; Isi Intent) dalam satu ZIP. Tidak perlu input file.</div>'+
     '<div class="field"><label>Access Token Google <span style="font-weight:400;color:var(--text2)">(opsional)</span></label><input type="text" id="f_token" placeholder="Kosongkan bila pakai service-account.json"></div>';
  }
  if(n===4){
    const d2 = STATE.steps[2] && STATE.steps[2].status==='done';
    const d3 = STATE.steps[3] && STATE.steps[3].status==='done';
    const autoOk = d2 && d3;
    return '<div class="field"><label>Server URL <span style="font-weight:400;color:var(--text2)">(opsional)</span></label><input type="text" id="f_ngrok" placeholder="Biarkan kosong (mode Colab)"><div class="hint">Mode Colab all-in-one: <b>kosongkan saja</b> \u2014 otomatis pakai localhost:8000. Isi hanya bila FastAPI di server terpisah.</div></div>'+
     srcToggle('s4',[{mode:'auto',label:'Otomatis (Step 2 + 3)',enabled:autoOk},{mode:'manual',label:'Unggah 3 file'}])+
     '<div class="field srcfield" data-group="s4" data-mode="auto"><div class="hint">Workbook utama diambil dari <b>Step 2</b> (sheet Fallback). Training Phrase &amp; Intent diekstrak otomatis dari ZIP <b>Step 3</b>.'+(autoOk?'':' <b style="color:var(--red)">Jalankan Step 2 &amp; 3 dulu, atau pilih Unggah 3 file.</b>')+'</div></div>'+
     '<div class="field srcfield" data-group="s4" data-mode="manual"><label>Workbook utama (sheet "Fallback")</label><input type="file" id="f_main" accept=".xlsx"><div class="hint">Hasil Step 2.</div></div>'+
     '<div class="field srcfield" data-group="s4" data-mode="manual"><label>Training Phrase (.xlsx)</label><input type="file" id="f_train" accept=".xlsx"></div>'+
     '<div class="field srcfield" data-group="s4" data-mode="manual"><label>Intent (.xlsx)</label><input type="file" id="f_content" accept=".xlsx"></div>';
  }
  if(n===5){
    const d4 = STATE.steps[4] && STATE.steps[4].status==='done';
    return '<div class="field"><label>Server URL <span style="font-weight:400;color:var(--text2)">(opsional)</span></label><input type="text" id="f_ngrok" placeholder="Biarkan kosong (mode Colab)"><div class="hint">Mode Colab all-in-one: <b>kosongkan saja</b> \u2014 otomatis pakai localhost:8000.</div></div>'+
     srcToggle('s5',[{mode:'prev',label:'Hasil Step 4',enabled:d4},{mode:'upload',label:'Unggah XLSX'}])+
     '<div class="field srcfield" data-group="s5" data-mode="prev"><div class="hint">Memakai hasil <b>Step 4</b> (Top-5) dari server.</div></div>'+
     '<div class="field srcfield" data-group="s5" data-mode="upload"><label>File XLSX Top 5</label><input type="file" id="f_xlsx" accept=".xlsx"></div>';
  }
  if(n===7){
    const d6 = STATE.steps[6] && STATE.steps[6].status==='done';
    const d5 = STATE.steps[5] && STATE.steps[5].status==='done';
    const autoOk = d6 || d5 || (STATE.steps[2] && STATE.steps[2].status==='done');
    return '<div class="field"><label>Server URL <span style="font-weight:400;color:var(--text2)">(opsional)</span></label><input type="text" id="f_ngrok" placeholder="Biarkan kosong (mode Colab)"><div class="hint">Menilai apakah <b>bot response</b> benar-benar menjawab <b>pertanyaan user</b> pada interaksi Non Fallback. Kosongkan untuk localhost:8000.</div></div>'+
     srcToggle('s7',[{mode:'auto',label:'Otomatis (hasil Step 6)',enabled:autoOk},{mode:'manual',label:'Unggah XLSX'}])+
     '<div class="field srcfield" data-group="s7" data-mode="auto"><div class="hint">Membaca sheet <b>Non Fallback</b> dari hasil <b>Step 6</b> (atau hasil terbaru yang tersedia), lalu menambah sheet <b>Analisis MKTA</b>.'+(autoOk?'':' <b style="color:var(--red)">Jalankan minimal Step 2 dulu.</b>')+'</div></div>'+
     '<div class="field srcfield" data-group="s7" data-mode="manual"><label>Workbook (.xlsx, punya sheet "Non Fallback")</label><input type="file" id="f_xlsx" accept=".xlsx"></div>';
  }
  if(n===12){
    return '<div class="field"><label>File JSON AWE Avaya <span style="font-weight:400;color:var(--text2)">(boleh beberapa)</span></label><input type="file" id="f_avjson" accept=".json,application/json" multiple><div class="hint">Pilih satu atau beberapa file sekaligus. Rentang tanggal lanjutan otomatis <b>digabung</b> &amp; dedup berdasarkan <b>sid</b>.</div></div>';
  }
  if(n===13){
    return '<div class="field"><label>Access Token Google <span style="font-weight:400;color:var(--text2)">(opsional)</span></label><input type="text" id="f_token" placeholder="Kosongkan bila pakai service-account.json"><div class="hint">Menarik seluruh intent aktif dari Dialogflow <b>persis seperti Step 3</b> \u2014 menghasilkan Training Phrase + Intent dalam ZIP. Dipakai untuk memetakan apakah pertanyaan pelanggan sudah tercover chatbot.</div></div>';
  }
  if(n===14){
    const d12 = STATE.steps[12] && STATE.steps[12].status==='done';
    const d13 = STATE.steps[13] && STATE.steps[13].status==='done';
    const d3  = STATE.steps[3]  && STATE.steps[3].status==='done';
    const autoOk = d12 && (d13 || d3);
    return '<div class="field"><label>Server URL <span style="font-weight:400;color:var(--text2)">(opsional)</span></label><input type="text" id="f_ngrok" placeholder="Biarkan kosong (mode Colab)"><div class="hint">Mode Colab all-in-one: <b>kosongkan saja</b> \u2014 otomatis pakai localhost:8000.</div></div>'+
     srcToggle('s14',[{mode:'auto',label:'Otomatis (Step 12 + 13)',enabled:autoOk},{mode:'manual',label:'Unggah Training + Intent'}])+
     '<div class="field srcfield" data-group="s14" data-mode="auto"><div class="hint">JSON gabungan dari <b>Step 12</b>; Training Phrase &amp; Intent dari <b>Step 13</b>'+(d13?'':(d3?' (memakai hasil Step 3)':''))+'.'+(autoOk?'':' <b style="color:var(--red)">Jalankan Step 12 &amp; 13 dulu, atau pilih Unggah manual.</b>')+'</div></div>'+
     '<div class="field srcfield" data-group="s14" data-mode="manual"><label>Training Phrase (.xlsx)</label><input type="file" id="f_train" accept=".xlsx"></div>'+
     '<div class="field srcfield" data-group="s14" data-mode="manual"><label>Intent (.xlsx)</label><input type="file" id="f_content" accept=".xlsx"></div>';
  }
  if(n===15){
    return '<div class="hint">Membangun <b>dashboard HTML interaktif</b> dari hasil <b>Step 14</b>. Tidak perlu input. Bila gagal, pesan error asli dari server akan tampil di sini (bukan lagi "HTTP 500" kosong).</div>';
  }
  if(n===16){
    return '<div class="hint">Membangun <b>workbook Excel</b> multi-sheet (Ringkasan, Percakapan, Agent, Pelanggan, Kandidat Intent) dari hasil <b>Step 14</b>. Tidak perlu input.</div>';
  }
  return '';
}

function openModal(n){
  const st = STEPS.find(x=>x.n===n);
  const modal = document.getElementById('modal');
  modal.classList.remove('wide');
  if(st.type==='step6'){ openModal6(st); return; }
  if(st.type==='step8'){ openModal8(st); return; }
  if(st.type==='step9'){ openModal9(st); return; }
  if(st.type==='step10'){ openModal10(st); return; }
  if(st.type==='step11'){ openModal11(st); return; }
  const badge = st.type==='soon' ? '&#9679;' : n;
  let inner =
    '<div class="mhead"><div class="mbadge">'+badge+'</div>'+
      '<div><h2>'+esc(st.title)+'</h2><p>'+esc(st.sub)+'</p></div>'+
      '<button class="mx" id="mxBtn" title="Tutup">&times;</button></div>';

  if(st.type==='soon'){
    inner += '<div class="soon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg><div><b>Step '+n+' belum tersedia</b></div><div style="margin-top:6px;font-size:13px">Kerangka UI &amp; mekanisme "lanjut" sudah siap. Tinggal tambahkan logika step ini di index.php.</div></div>'+
      '<div class="mfoot"><button class="btn btn-sec" id="closeBtn2">Tutup</button></div>';
  } else {
    inner += '<div class="mbody" id="mbody">'+formHtml(n)+'</div>'+
      '<div class="status" id="mstatus"></div>'+
      '<div class="summary" id="msummary"></div>'+
      '<div class="mfoot">'+
        '<button class="btn" id="runBtn">Jalankan Step '+n+'</button>'+
        '<button class="btn btn-sec" id="dlBtn" style="display:none">Unduh Hasil</button>'+
        '<button class="btn btn-ok" id="nextBtn" style="display:none">Lanjut ke Step '+(n+1)+' \u2192</button>'+
      '</div>';
  }

  modal.innerHTML = inner;
  document.getElementById('overlay').classList.add('show');

  // Tutup HANYA lewat tombol X (atau tombol Tutup pada step soon)
  document.getElementById('mxBtn').onclick = closeModal;
  const cb2 = document.getElementById('closeBtn2'); if(cb2) cb2.onclick = closeModal;

  if(st.type!=='soon'){
    bindSourceToggle();
    prefillDates(n);
    if(n===4 || n===5 || n===14){ const el=document.getElementById('f_ngrok'); if(el && STATE.ngrok_url) el.value=STATE.ngrok_url; }
    document.getElementById('runBtn').onclick = ()=>runStep(n);
    // Jika step sudah pernah selesai, tampilkan ringkasannya
    const done = STATE.steps[n];
    if(done && done.status==='done'){ showSummary(n, done); showDone(n); }
  }
}

function prefillDates(n){
  if(n!==1) return;
  const d=new Date(); d.setDate(d.getDate()-1);
  const y=new Date(); y.setDate(y.getDate()-7);
  const fmt=x=>x.toISOString().slice(0,10);
  document.getElementById('f_end').value=fmt(d);
  document.getElementById('f_start').value=fmt(y);
}

function bindSourceToggle(){
  document.querySelectorAll('.srcbox').forEach(box=>{
    const group=box.dataset.group;
    const apply=(mode)=>{
      document.querySelectorAll('.srcfield[data-group="'+group+'"]').forEach(f=>{
        f.style.display = (f.dataset.mode===mode) ? '' : 'none';
      });
    };
    box.querySelectorAll('.srcbtn').forEach(b=>{
      b.onclick=()=>{
        if(b.disabled) return;
        box.querySelectorAll('.srcbtn').forEach(x=>x.classList.remove('on'));
        b.classList.add('on');
        apply(b.dataset.mode);
      };
    });
    const on=box.querySelector('.srcbtn.on');
    apply(on?on.dataset.mode:'');
  });
}

function closeModal(){ document.getElementById('overlay').classList.remove('show'); }

function setStatus(kind, html){
  const s=document.getElementById('mstatus');
  s.className='status show '+kind;
  s.innerHTML=html;
}

function showSummary(n, art){
  const box=document.getElementById('msummary');
  const sm=art.summary||{};
  let rows='';
  rows+='<div class="row"><span class="k">Nama file</span><span class="v">'+esc(art.name)+'</span></div>';
  for(const k in sm){
    let v=sm[k];
    if(Array.isArray(v)){ if(!v.length) continue; v=v.join(' | '); }
    if(v===null||v==='') continue;
    rows+='<div class="row"><span class="k">'+esc(k.replace(/_/g," "))+'</span><span class="v">'+esc(v)+'</span></div>';
  }
  box.innerHTML=rows;
  box.classList.add('show');
}

function showDone(n){
  document.getElementById('dlBtn').style.display='';
  document.getElementById('dlBtn').onclick=()=>{ window.location = ENDPOINT+'?action=download&run='+encodeURIComponent(RUN)+'&step='+n; };
  const next=STEPS.find(x=>x.n===n+1);
  const nb=document.getElementById('nextBtn');
  if(next){ nb.style.display=''; nb.onclick=()=>openModal(n+1); }
  const rb=document.getElementById('runBtn'); if(rb) rb.textContent='Jalankan Ulang Step '+n;
  if(n===15){
    const dl=document.getElementById('dlBtn');
    if(dl){ dl.style.display=''; dl.textContent='Buka Dashboard'; dl.onclick=()=>window.open(ENDPOINT+'?action=download&run='+encodeURIComponent(RUN)+'&step=15&part=avayadash','_blank'); }
  }
}

function currentMode(group){
  const box=document.querySelector('.srcbox[data-group="'+group+'"]');
  if(!box) return '';
  const on=box.querySelector('.srcbtn.on');
  return on?on.dataset.mode:'';
}

function runStep(n){
  const fd=new FormData();
  try{
    if(n===1){
      fd.append('start_date', val('f_start'));
      fd.append('end_date', val('f_end'));
      fd.append('bahasa', val('f_lang'));
      if(val('f_token')) fd.append('access_token', val('f_token'));
    } else if(n===2){
      if(currentMode('s2')==='prev'){ fd.append('from_step','1'); }
      else { const f=file('f_json'); if(!f) throw 'Pilih file JSON dulu.'; fd.append('json_file', f); }
    } else if(n===3){
      if(val('f_token')) fd.append('access_token', val('f_token'));
    } else if(n===4){
      if(val('f_ngrok')) STATE.ngrok_url = val('f_ngrok');
      fd.append('ngrok_url', val('f_ngrok'));
      const mode = currentMode('s4'); fd.append('mode', mode);
      if(mode==='manual'){
        const m=file('f_main'), t=file('f_train'), c=file('f_content');
        if(!m||!t||!c) throw 'Unggah ketiga file: workbook utama, Training Phrase, dan Intent.';
        fd.append('main_file', m); fd.append('training_file', t); fd.append('content_file', c);
      }
    } else if(n===5){
      if(val('f_ngrok')) STATE.ngrok_url = val('f_ngrok');
      fd.append('ngrok_url', val('f_ngrok'));
      if(currentMode('s5')==='prev'){ fd.append('from_step','4'); }
      else { const f=file('f_xlsx'); if(!f) throw 'Pilih file XLSX dulu.'; fd.append('xlsx_file', f); }
    } else if(n===7){
      if(val('f_ngrok')) STATE.ngrok_url = val('f_ngrok');
      fd.append('ngrok_url', val('f_ngrok'));
      const mode = currentMode('s7'); fd.append('mode', mode);
      if(mode==='manual'){ const f=file('f_xlsx'); if(!f) throw 'Pilih file XLSX (punya sheet Non Fallback).'; fd.append('xlsx_file', f); }
    } else if(n===12){
      const el=document.getElementById('f_avjson'); const fs=(el&&el.files)?el.files:[];
      if(!fs.length) throw 'Pilih minimal satu file JSON AWE Avaya.';
      for(let i=0;i<fs.length;i++) fd.append('json_files[]', fs[i]);
    } else if(n===13){
      if(val('f_token')) fd.append('access_token', val('f_token'));
    } else if(n===14){
      if(val('f_ngrok')) STATE.ngrok_url = val('f_ngrok');
      fd.append('ngrok_url', val('f_ngrok'));
      const mode = currentMode('s14'); fd.append('mode', mode);
      if(mode==='manual'){ const t=file('f_train'), c=file('f_content'); if(!t||!c) throw 'Unggah Training Phrase dan Intent.'; fd.append('training_file', t); fd.append('content_file', c); }
    } else if(n===15 || n===16){
      fd.append('ngrok_url', STATE.ngrok_url || '');
    }
  }catch(msg){ setStatus('err', esc(msg)); return; }

  const rb=document.getElementById('runBtn'); rb.disabled=true;
  document.getElementById('msummary').classList.remove('show');
  setStatus('run','<span class="sp"></span>Memproses Step '+n+'... Jangan tutup jendela ini.');

  if(n===14){ return runStep14Async(fd, rb); }

  api('step'+n, {method:'POST', body:fd}).then(res=>{
    rb.disabled=false;
    if(res && res.ok){
      STATE.steps[n]=res.artifact;
      setStatus('ok','\u2714 Step '+n+' selesai.');
      showSummary(n, res.artifact);
      showDone(n);
      renderRail();
    } else {
      const msg=(res&&res.error)?res.error:'Terjadi kesalahan.';
      setStatus('err','\u26A0 '+esc(msg));
      STATE.steps[n]={status:'error'};
      renderRail();
    }
  }).catch(e=>{
    // Kemungkinan gateway timeout (ngrok 503) walau proses server masih lanjut.
    setStatus('run','<span class="sp"></span>Gateway timeout, tapi server mungkin masih memproses Step '+n+'. Menunggu hasil otomatis... (jangan tutup jendela)');
    pollStepDone(n,
      (st)=>{ rb.disabled=false; STATE.steps[n]=st; setStatus('ok','\u2714 Step '+n+' selesai (terdeteksi otomatis).'); showSummary(n, st); showDone(n); renderRail(); },
      (why)=>{ rb.disabled=false; setStatus('err','\u26A0 '+esc(e.message||e)+' ('+esc(why)+'). Coba refresh halaman \u2014 bila step sudah hijau, hasil sudah tersimpan.'); }
    );
  });
}

// ===== STEP 14 async: jalankan pipeline di latar belakang + polling progres =====
// Menyelesaikan masalah gateway timeout (ngrok/Colab memutus koneksi panjang):
// request start/progress/fetch semuanya singkat, proses berat berjalan di thread
// server. Progres (tahap + x/y percakapan + detik) tampil live di modal.
function runStep14Async(fd, rb){
  setStatus('run','<span class="sp"></span>Memulai analisis di server (mode latar belakang, aman dari timeout)...');
  api('step14start', {method:'POST', body:fd}).then(res=>{
    if(!res || !res.ok || !res.job_id){ throw new Error((res&&res.error)?res.error:'Gagal memulai job.'); }
    STATE.avaya_job = res.job_id;
    setStatus('run','<span class="sp"></span>Job dimulai (id '+esc(res.job_id)+'). Memantau progres...');
    pollAvaya14(res.job_id, rb, 0);
  }).catch(e=>{ rb.disabled=false; setStatus('err','\u26A0 '+esc(e.message||e)); });
}

function pollAvaya14(job, rb, tries){
  const max=720; // ~60 menit @5s
  const extra = STATE.ngrok_url ? '&ngrok_url='+encodeURIComponent(STATE.ngrok_url) : '';
  api('step14progress&job='+encodeURIComponent(job)+extra, {}).then(res=>{
    const p = (res && res.progress) ? res.progress : null;
    if(p && p.found){
      if(p.error){ rb.disabled=false; setStatus('err','\u26A0 Server melaporkan error:<br><pre style="white-space:pre-wrap;font-size:11px;max-height:220px;overflow:auto">'+esc(p.error)+'</pre>'); return; }
      const tot=p.total||0, dn=p.done||0, pct=tot?Math.round(dn/tot*100):0;
      const bar = tot? (' \u2014 '+dn+'/'+tot+' ('+pct+'%)') : '';
      setStatus('run','<span class="sp"></span>['+Math.round(p.elapsed||0)+'s] '+esc(p.stage||'memproses')+bar+'<br><span style="font-size:11px;color:var(--text2)">Berjalan di latar belakang \u2014 aman dari gateway timeout.</span>');
      if(p.finished){ fetchAvaya14(job, rb); return; }
    } else {
      setStatus('run','<span class="sp"></span>Menunggu job... (bila server baru di-restart, jalankan ulang Step 14)');
    }
    if(tries>=max){ rb.disabled=false; setStatus('err','\u26A0 Waktu tunggu habis (60 menit). Cek log Colab untuk detail.'); return; }
    setTimeout(()=>pollAvaya14(job, rb, tries+1), 5000);
  }).catch(e=>{
    if(tries>=max){ rb.disabled=false; setStatus('err','\u26A0 '+esc(e.message||e)); return; }
    setTimeout(()=>pollAvaya14(job, rb, tries+1), 5000);
  });
}

function fetchAvaya14(job, rb){
  const extra = STATE.ngrok_url ? '&ngrok_url='+encodeURIComponent(STATE.ngrok_url) : '';
  setStatus('run','<span class="sp"></span>Analisis selesai. Mengambil &amp; menyimpan hasil...');
  api('step14fetch&job='+encodeURIComponent(job)+extra, {}).then(res=>{
    if(res && res.pending){ setTimeout(()=>fetchAvaya14(job, rb), 3000); return; }
    rb.disabled=false;
    if(res && res.ok && res.artifact){
      STATE.steps[14]=res.artifact;
      setStatus('ok','\u2714 Step 14 selesai.');
      showSummary(14, res.artifact);
      showDone(14);
      renderRail();
    } else {
      setStatus('err','\u26A0 '+esc((res&&res.error)?res.error:'Gagal mengambil hasil.'));
    }
  }).catch(e=>{ rb.disabled=false; setStatus('err','\u26A0 '+esc(e.message||e)); });
}

/* ================= STEP 6: cross-check manual ================= */
let STEP6 = {rows:[]};

function openModal6(st){
  const modal=document.getElementById('modal');
  modal.classList.add('wide');
  modal.innerHTML =
    '<div class="mhead"><div class="mbadge">6</div>'+
      '<div><h2>'+esc(st.title)+'</h2><p>'+esc(st.sub)+'</p></div>'+
      '<button class="mx" id="mxBtn" title="Tutup">&times;</button></div>'+
    '<div class="s6bar">'+
      '<div class="fg"><label>Catatan LLM</label><select id="f6cat"><option value="">Semua</option></select></div>'+
      '<div class="fg"><label>Confidence</label><select id="f6conf"><option value="">Semua</option><option>TINGGI</option><option>SEDANG</option><option>RENDAH</option></select></div>'+
      '<div class="fg"><label>Skor_Deteksi min (%)</label><input type="number" id="f6skor" min="0" max="100" step="1" style="width:120px" placeholder="0"></div>'+
      '<div class="fg"><label>Cari pertanyaan</label><input type="text" id="f6q" placeholder="kata kunci..."></div>'+
      '<span class="count" id="s6count"></span>'+
    '</div>'+
    '<div class="status" id="mstatus"></div>'+
    '<div class="s6wrap"><table class="s6table"><thead><tr>'+
      '<th>Pertanyaan User</th><th>Catatan LLM</th><th>Intent Judgement LLM</th><th>Isi Intent</th><th>Skor</th><th>Conf</th>'+
    '</tr></thead><tbody id="s6body"></tbody></table></div>'+
    '<div class="mfoot">'+
      '<button class="btn" id="s6save">Simpan Perubahan</button>'+
      '<button class="btn btn-sec" id="dlBtn" style="display:none">Unduh Hasil</button>'+
      '<button class="btn btn-ok" id="nextBtn" style="display:none">Lanjut ke Step 7 \u2192</button>'+
    '</div>';
  document.getElementById('overlay').classList.add('show');
  document.getElementById('mxBtn').onclick=closeModal;
  document.getElementById('s6save').onclick=saveStep6;
  ['f6cat','f6conf','f6skor','f6q'].forEach(id=>{ const el=document.getElementById(id); el.oninput=renderStep6; el.onchange=renderStep6; });
  const wrap=document.querySelector('.s6wrap'); if(wrap) wrap.onscroll=closeS6Menus;
  if(!window.__s6docbound){ window.__s6docbound=true; document.addEventListener('mousedown', function(e){ if(!(e.target.closest && e.target.closest('.s6combo'))) closeS6Menus(); }); }
  loadStep6();
}

function loadStep6(){
  setStatus('run','<span class="sp"></span>Memuat data dari hasil Step 5...');
  STEP6.rows=[];
  api('step6load',{}).then(res=>{
    if(!res || !res.ok){ setStatus('err','\u26A0 '+esc((res&&res.error)||'Gagal memuat.')); return; }
    STEP6.rows = res.rows||[];
    STEP6.rows.forEach(r=>{ r.edited=false; syncRowDerived(r); });
    const cats=[...new Set(STEP6.rows.map(r=>r.catatan).filter(Boolean))];
    const sel=document.getElementById('f6cat');
    cats.forEach(c=>{ const o=document.createElement('option'); o.value=c; o.textContent=c; sel.appendChild(o); });
    document.getElementById('mstatus').className='status';
    renderStep6();
    const done=STATE.steps[6];
    if(done && done.status==='done'){ showDone(6); }
  }).catch(e=>setStatus('err','\u26A0 '+esc(e.message||e)));
}

function syncRowDerived(r){
  const opt=(r.options||[]).find(o=>o.id===r.intent);
  if(opt){ r.isi=opt.ans; r.skor=opt.skor; r.conf=opt.conf; }
  else { r.isi=''; r.skor=''; r.conf=''; }
}

function parseSkor(s){ const v=parseFloat(String(s==null?'':s).replace('%','')); return isNaN(v)?-1:v; }

function renderStep6(){
  const body=document.getElementById('s6body'); if(!body) return;
  const fcat=document.getElementById('f6cat').value;
  const fconf=document.getElementById('f6conf').value;
  const fskor=parseFloat(document.getElementById('f6skor').value);
  const fq=document.getElementById('f6q').value.trim().toLowerCase();
  const CAP=400;
  let shown=0, matched=0;
  const parts=[];
  STEP6.rows.forEach((r,i)=>{
    if(fcat && r.catatan!==fcat) return;
    if(fconf && (r.conf||'')!==fconf) return;
    if(!isNaN(fskor) && parseSkor(r.skor) < fskor) return;
    if(fq && !(r.pertanyaan||'').toLowerCase().includes(fq)) return;
    matched++;
    if(shown>=CAP) return;
    shown++;
    const pill = r.catatan==='TINDAK LANJUT'?'t':(r.catatan==='PERTANYAAN TIDAK MANDIRI'?'n':'m');
    parts.push(
      '<tr>'+
      '<td class="s6q">'+esc(r.pertanyaan||'')+'</td>'+
      '<td><span class="s6pill '+pill+'">'+esc(r.catatan||'-')+'</span></td>'+
      '<td><div class="s6combo"><input class="s6intent'+(r.edited?' edited':'')+'" data-i="'+i+'" value="'+esc(r.intent||'')+'" autocomplete="off"><button type="button" class="s6arrow" data-i="'+i+'" tabindex="-1">\u25be</button><div class="s6menu" id="menu'+i+'"></div></div></td>'+
      '<td><div class="s6isi" id="isi'+i+'">'+esc(r.isi||'')+'</div></td>'+
      '<td id="skor'+i+'">'+esc(r.skor||'')+'</td>'+
      '<td id="conf'+i+'">'+esc(r.conf||'')+'</td>'+
      '</tr>'
    );
  });
  body.innerHTML=parts.join('');
  body.querySelectorAll('.s6intent').forEach(inp=>{ const i=parseInt(inp.dataset.i,10); inp.oninput=()=>onIntentChange(i, inp.value); inp.onfocus=()=>openS6Menu(i); });
  body.querySelectorAll('.s6arrow').forEach(btn=>{ const i=parseInt(btn.dataset.i,10); btn.onclick=(e)=>{ e.preventDefault(); const m=document.getElementById('menu'+i); if(m && m.classList.contains('open')) closeS6Menus(); else openS6Menu(i); }; });
  document.getElementById('s6count').textContent = matched+' baris'+(matched>CAP?(' (tampil '+CAP+', persempit dgn filter)'):'');
}

function onIntentChange(i, value){
  const r=STEP6.rows[i]; if(!r) return;
  r.intent=value; r.edited=true;
  syncRowDerived(r);
  const isi=document.getElementById('isi'+i); if(isi) isi.textContent=r.isi||'';
  const sk=document.getElementById('skor'+i); if(sk) sk.textContent=r.skor||'';
  const cf=document.getElementById('conf'+i); if(cf) cf.textContent=r.conf||'';
  const inp=document.querySelector('.s6intent[data-i="'+i+'"]'); if(inp) inp.classList.add('edited');
}

/* ================= STEP 8: filter QA Conf -> Qwen ================= */
let STEP8 = {counts:[], total:0};

function step8Mode(){
  const on=document.querySelector('#s8toggle .srcbtn.on');
  return on?on.dataset.mode:'auto';
}

function openModal8(st){
  const modal=document.getElementById('modal');
  modal.innerHTML =
    '<div class="mhead"><div class="mbadge">8</div>'+
      '<div><h2>'+esc(st.title)+'</h2><p>'+esc(st.sub)+'</p></div>'+
      '<button class="mx" id="mxBtn" title="Tutup">&times;</button></div>'+
    '<div class="mbody">'+
      '<div class="field"><label>Server URL <span style="font-weight:400;color:var(--text2)">(opsional)</span></label><input type="text" id="f_ngrok" placeholder="Biarkan kosong (mode Colab)"><div class="hint">Menilai baris QA Conf rendah dengan Qwen (kolom PUTUSAN &amp; ALASAN). Diproses bertahap agar tak kena timeout. Kosongkan untuk localhost:8000.</div></div>'+
      '<div class="srcbox" id="s8toggle">'+
        '<button type="button" class="srcbtn on" data-mode="auto">Otomatis (hasil Step 7)</button>'+
        '<button type="button" class="srcbtn" data-mode="manual">Unggah XLSX</button>'+
      '</div>'+
      '<div class="field" id="s8upwrap" style="display:none"><label>File XLSX (ber-sheet "QA Conf MKTA")</label><input type="file" id="f_x8" accept=".xlsx"><button class="btn btn-sec" id="s8muat" style="margin-top:8px">Muat data</button></div>'+
      '<div class="field"><label>Ambang Skor Pemrosesan Bahasa (QA Conf) &mdash; proses baris di bawah nilai ini</label>'+
        '<div id="s8list" style="margin-top:6px"><div class="hint">Memuat...</div></div>'+
      '</div>'+
    '</div>'+
    '<div class="status" id="mstatus"></div>'+
    '<div class="mfoot">'+
      '<button class="btn" id="s8run" disabled>Lempar ke Qwen</button>'+
      '<button class="btn btn-sec" id="dlBtn" style="display:none">Unduh Hasil</button>'+
      '<button class="btn btn-ok" id="nextBtn" style="display:none">Lanjut ke Step 9 \u2192</button>'+
    '</div>';
  document.getElementById('overlay').classList.add('show');
  document.getElementById('mxBtn').onclick=closeModal;
  if(STATE.ngrok_url){ const el=document.getElementById('f_ngrok'); if(el) el.value=STATE.ngrok_url; }
  document.getElementById('s8run').onclick=runStep8;
  document.getElementById('s8muat').onclick=()=>loadStep8();
  document.querySelectorAll('#s8toggle .srcbtn').forEach(b=>{
    b.onclick=()=>{
      document.querySelectorAll('#s8toggle .srcbtn').forEach(x=>x.classList.remove('on'));
      b.classList.add('on');
      const manual=b.dataset.mode==='manual';
      document.getElementById('s8upwrap').style.display=manual?'':'none';
      document.getElementById('s8run').disabled=true;
      if(manual){ document.getElementById('s8list').innerHTML='<div class="hint">Unggah file lalu klik <b>Muat data</b>.</div>'; document.getElementById('mstatus').className='status'; }
      else { loadStep8(); }
    };
  });
  loadStep8();
}

function loadStep8(){
  const mode=step8Mode();
  const fd=new FormData(); fd.append('mode', mode);
  if(mode==='manual'){ const f=file('f_x8'); if(!f){ setStatus('err','\u26A0 Pilih file XLSX dulu.'); return; } fd.append('xlsx_file', f); }
  setStatus('run','<span class="sp"></span>Menghitung jumlah baris per ambang...');
  api('step8load',{method:'POST', body:fd}).then(res=>{
    if(!res || !res.ok){ setStatus('err','\u26A0 '+esc((res&&res.error)||'Gagal memuat.')); return; }
    STEP8.counts = res.counts||[]; STEP8.total = res.total||0;
    document.getElementById('mstatus').className='status';
    renderStep8();
    const done=STATE.steps[8]; if(done && done.status==='done') showDone(8);
  }).catch(e=>setStatus('err','\u26A0 '+esc(e.message||e)));
}

function renderStep8(){
  const box=document.getElementById('s8list'); if(!box) return;
  const fmt=t=>('< '+String(t).replace('.',','));
  let html='<div style="font-size:12.5px;color:var(--text2);margin-bottom:8px">Total baris QA Conf MKTA: <b>'+STEP8.total+'</b></div>';
  html+='<div style="display:flex;flex-direction:column;gap:2px">';
  STEP8.counts.forEach((c,idx)=>{
    const checked = (Math.abs(c.th-0.6)<1e-9) ? ' checked' : '';
    html+='<label style="display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--border);border-radius:8px;cursor:pointer">'+
      '<input type="radio" name="s8th" value="'+c.th+'"'+checked+'>'+
      '<span style="font-weight:600;min-width:70px">'+fmt(c.th)+'</span>'+
      '<span style="color:var(--text2);font-size:12.5px">'+c.count+' baris</span>'+
      '</label>';
  });
  html+='</div>';
  box.innerHTML=html;
  box.querySelectorAll('input[name=s8th]').forEach(r=>{ r.onchange=updateStep8Btn; });
  updateStep8Btn();
  document.getElementById('s8run').disabled=false;
}

function step8Selected(){
  const r=document.querySelector('input[name=s8th]:checked');
  if(!r) return null;
  const th=parseFloat(r.value);
  const c=STEP8.counts.find(x=>Math.abs(x.th-th)<1e-9);
  return {th:th, count:c?c.count:0};
}

function updateStep8Btn(){
  const s=step8Selected(); const btn=document.getElementById('s8run'); if(!btn) return;
  btn.textContent = s ? ('Lempar '+s.count+' baris ke Qwen') : 'Lempar ke Qwen';
}

function runStep8(){
  const s=step8Selected(); if(!s){ setStatus('err','\u26A0 Pilih ambang dulu.'); return; }
  if(s.count===0){ setStatus('err','\u26A0 Tidak ada baris pada ambang ini.'); return; }
  const btn=document.getElementById('s8run'); btn.disabled=true;
  const ngrok=val('f_ngrok'); if(ngrok) STATE.ngrok_url=ngrok;
  const mode=step8Mode();
  const target=s.count; let processedTotal=0;
  const doChunk=()=>{
    const fd=new FormData();
    fd.append('ngrok_url', ngrok);
    fd.append('threshold', String(s.th));
    fd.append('mode', mode);
    setStatus('run','<span class="sp"></span>Memproses ke Qwen... '+processedTotal+'/'+target+' selesai. Jangan tutup jendela ini.');
    api('step8',{method:'POST', body:fd}).then(res=>{
      if(!res || !res.ok){ btn.disabled=false; setStatus('err','\u26A0 '+esc((res&&res.error)||'Gagal memproses.')); return; }
      STATE.steps[8]=res.artifact;
      processedTotal += (res.processed||0);
      if(res.done){
        btn.disabled=false;
        setStatus('ok','\u2714 Putusan selesai. Diproses '+processedTotal+' baris. File siap diunduh.');
        showDone(8); renderRail();
      } else if((res.processed||0)>0){
        doChunk();
      } else {
        btn.disabled=false;
        setStatus('err','\u26A0 0 baris terproses pada chunk ini, tetapi sisa '+(res.remaining||0)+'. Progres tersimpan \u2014 klik lagi untuk mencoba melanjutkan, atau cek log server.');
        showDone(8); renderRail();
      }
    }).catch(e=>{ btn.disabled=false; setStatus('err','\u26A0 '+esc(e.message||e)+' (progres tersimpan; klik lagi untuk lanjut)'); });
  };
  doChunk();
}

/* ================= STEP 9: analisis manual MKTA ================= */
let STEP9 = {rows:[]};

function openModal9(st){
  const modal=document.getElementById('modal');
  modal.classList.add('wide');
  modal.innerHTML =
    '<div class="mhead"><div class="mbadge">9</div>'+
      '<div><h2>'+esc(st.title)+'</h2><p>'+esc(st.sub)+'</p></div>'+
      '<button class="mx" id="mxBtn" title="Tutup">&times;</button></div>'+
    '<div class="s6bar">'+
      '<div class="fg"><label>Skor Bahasa &lt; (ambang sheet)</label><input type="number" id="f9qa" min="0" max="1" step="0.05" value="0.5" style="width:110px"></div>'+
      '<div class="fg"><label>PUTUSAN</label><select id="f9put"><option value="">Semua</option></select></div>'+
      '<div class="fg"><label>Kategori Mesin</label><select id="f9kat"><option value="">Semua</option></select></div>'+
      '<div class="fg"><label>Skor Dialogflow \u2264</label><input type="number" id="f9df" min="0" max="1" step="0.05" placeholder="0" style="width:100px"></div>'+
      '<div class="fg"><label>Skor NLI \u2264</label><input type="number" id="f9nli" min="0" max="1" step="0.05" placeholder="0" style="width:100px"></div>'+
      '<div class="fg"><label>Cari pertanyaan</label><input type="text" id="f9q" placeholder="kata kunci..."></div>'+
      '<span class="count" id="s9count"></span>'+
    '</div>'+
    '<div class="status" id="mstatus"></div>'+
    '<div class="s6wrap"><table class="s6table"><thead><tr>'+
      '<th>Prioritas</th><th>Pertanyaan User</th><th>Intent (Bot)</th><th>Kategori Mesin</th><th>Skor Bahasa</th><th>Skor DF</th><th>NLI</th><th>PUTUSAN &amp; Alasan</th><th>Kandidat / Terdekat</th><th>Intent Seharusnya</th>'+
    '</tr></thead><tbody id="s9body"></tbody></table></div>'+
    '<div class="mfoot">'+
      '<button class="btn" id="s9save">Simpan ke sheet Analisis MKTA</button>'+
      '<button class="btn btn-sec" id="dlBtn" style="display:none">Unduh Hasil</button>'+
      '<button class="btn btn-ok" id="nextBtn" style="display:none">Lanjut ke Step 10 \u2192</button>'+
    '</div>';
  document.getElementById('overlay').classList.add('show');
  document.getElementById('mxBtn').onclick=closeModal;
  document.getElementById('s9save').onclick=saveStep9;
  ['f9qa','f9put','f9kat','f9df','f9nli','f9q'].forEach(id=>{ const el=document.getElementById(id); el.oninput=renderStep9; el.onchange=renderStep9; });
  loadStep9();
}

function loadStep9(){
  setStatus('run','<span class="sp"></span>Memuat data dari hasil Step 8...');
  STEP9.rows=[];
  api('step9load',{}).then(res=>{
    if(!res || !res.ok){ setStatus('err','\u26A0 '+esc((res&&res.error)||'Gagal memuat.')); return; }
    STEP9.rows = res.rows||[];
    STEP9.rows.forEach(r=>{ r.edited=false; });
    const puts=[...new Set(STEP9.rows.map(r=>r.putusan).filter(Boolean))];
    const sel=document.getElementById('f9put');
    puts.forEach(p=>{ const o=document.createElement('option'); o.value=p; o.textContent=p; sel.appendChild(o); });
    const kats=[...new Set(STEP9.rows.map(r=>r.kategori).filter(Boolean))];
    const selk=document.getElementById('f9kat');
    kats.forEach(k=>{ const o=document.createElement('option'); o.value=k; o.textContent=k; selk.appendChild(o); });
    document.getElementById('mstatus').className='status';
    renderStep9();
    const done=STATE.steps[9]; if(done && done.status==='done') showDone(9);
  }).catch(e=>setStatus('err','\u26A0 '+esc(e.message||e)));
}

function s9num(v){ const n=parseFloat(String(v==null?'':v).replace(',','.')); return isNaN(n)?null:n; }
function s9fmt(v){ const n=s9num(v); return n===null ? esc(String(v==null?'':v)) : n.toFixed(2); }

function renderStep9(){
  const body=document.getElementById('s9body'); if(!body) return;
  const thr=parseFloat(document.getElementById('f9qa').value);
  const fput=document.getElementById('f9put').value;
  const fkat=document.getElementById('f9kat').value;
  const fdf=parseFloat(document.getElementById('f9df').value);
  const fnli=parseFloat(document.getElementById('f9nli').value);
  const fq=document.getElementById('f9q').value.trim().toLowerCase();
  const CAP=400; let shown=0, matched=0, underThr=0;
  const parts=[];
  const order = STEP9.rows.map((r,i)=>i);
  order.sort((a,b)=>{ const pa=s9num(STEP9.rows[a].prioritas), pb=s9num(STEP9.rows[b].prioritas); return (pb===null?-1:pb)-(pa===null?-1:pa); });
  order.forEach(i=>{
    const r=STEP9.rows[i];
    const qa=s9num(r.qa);
    const inThr = !isNaN(thr) ? (qa!==null && qa<thr) : true;
    if(inThr) underThr++;
    if(!inThr) return;
    if(fput && r.putusan!==fput) return;
    if(fkat && r.kategori!==fkat) return;
    if(!isNaN(fdf)){ const d=s9num(r.df); if(d===null || d>fdf) return; }
    if(!isNaN(fnli)){ const n=s9num(r.nli); if(n===null || n>fnli) return; }
    if(fq && !(r.pertanyaan||'').toLowerCase().includes(fq)) return;
    matched++;
    if(shown>=CAP) return; shown++;
    const kand = (r.kandidat||r.terdekat||'');
    const alasan = r.alasan ? '<div style="color:#9aa4b2;font-size:11px;margin-top:3px">'+esc(r.alasan)+'</div>' : '';
    parts.push(
      '<tr>'+
      '<td>'+esc(r.prioritas||'')+'</td>'+
      '<td class="s6q">'+esc(r.pertanyaan||'')+'</td>'+
      '<td class="s6q">'+esc(r.intent||'')+'</td>'+
      '<td>'+esc(r.kategori||'')+'</td>'+
      '<td>'+s9fmt(r.qa)+'</td>'+
      '<td>'+s9fmt(r.df)+'</td>'+
      '<td>'+s9fmt(r.nli)+'</td>'+
      '<td>'+esc(r.putusan||'')+alasan+'</td>'+
      '<td class="s6q">'+esc(kand)+'</td>'+
      '<td><input class="s6intent" data-i="'+i+'" value="'+esc(r.seharusnya||'')+'" placeholder="ketik intent..."></td>'+
      '</tr>'
    );
  });
  body.innerHTML=parts.join('');
  body.querySelectorAll('.s6intent').forEach(inp=>{ const i=parseInt(inp.dataset.i,10); inp.oninput=()=>{ STEP9.rows[i].seharusnya=inp.value; STEP9.rows[i].edited=true; inp.classList.add('edited'); }; });
  document.getElementById('s9count').textContent = matched+' tampil \u00b7 '+underThr+' akan disimpan (Skor<'+(isNaN(thr)?'-':thr)+')'+(matched>CAP?(' \u00b7 tampil '+CAP):'');
}

function saveStep9(){
  const thr=parseFloat(document.getElementById('f9qa').value);
  if(isNaN(thr)||thr<=0||thr>1){ setStatus('err','\u26A0 Isi ambang QA Conf yang valid (0-1).'); return; }
  const edits={};
  STEP9.rows.forEach(r=>{ if(r.edited) edits[String(r.row)] = r.seharusnya||''; });
  const fd=new FormData(); fd.append('threshold', String(thr)); fd.append('edits', JSON.stringify(edits));
  const btn=document.getElementById('s9save'); btn.disabled=true;
  setStatus('run','<span class="sp"></span>Menyimpan sheet Analisis MKTA (QA Conf < '+thr+')...');
  api('step9',{method:'POST', body:fd}).then(res=>{
    btn.disabled=false;
    if(res && res.ok){ STATE.steps[9]=res.artifact; setStatus('ok','\u2714 Tersimpan '+(res.baris||0)+' baris ke sheet Analisis MKTA. File siap diunduh.'); showDone(9); renderRail(); }
    else { setStatus('err','\u26A0 '+esc((res&&res.error)||'Gagal menyimpan.')); }
  }).catch(e=>{ btn.disabled=false; setStatus('err','\u26A0 '+esc(e.message||e)); });
}

/* ================= STEP 10: laporan LM + Pembaruan ================= */
function openModal10(st){
  const modal=document.getElementById('modal');
  modal.innerHTML =
    '<div class="mhead"><div class="mbadge">10</div>'+
      '<div><h2>'+esc(st.title)+'</h2><p>'+esc(st.sub)+'</p></div>'+
      '<button class="mx" id="mxBtn" title="Tutup">&times;</button></div>'+
    '<div class="mbody">'+
      '<div class="hint">Membuat <b>sheet LM</b> &amp; <b>sheet Pembaruan</b> dari hasil Step 9, lalu menghasilkan <b>3 file</b>: Excel Utama, CSV LM, dan CSV Pembaruan. Hanya baris <b>TINDAK LANJUT</b> (Fallback &amp; MKTA) yang masuk. TGL Penyusunan = tanggal rekaman tiap baris.</div>'+
      '<div class="field"><label>Nama Penyusun</label><input type="text" id="f10nama" placeholder="mis. SAMSUL HIDAYATULLAH" value="'+esc((STATE.steps[10]&&STATE.steps[10].penyusun)||'')+'"><div class="hint">Dipakai di kolom NAMA PENYUSUN (sheet &amp; CSV Pembaruan).</div></div>'+
    '</div>'+
    '<div class="status" id="mstatus"></div>'+
    '<div class="summary" id="msummary"></div>'+
    '<div class="mfoot" id="s10foot">'+
      '<button class="btn" id="s10run">Buat Laporan</button>'+
    '</div>';
  document.getElementById('overlay').classList.add('show');
  document.getElementById('mxBtn').onclick=closeModal;
  document.getElementById('s10run').onclick=runStep10;
  const done=STATE.steps[10];
  if(done && done.status==='done'){ showSummary(10, done); step10Downloads(); }
}

function step10Downloads(){
  const foot=document.getElementById('s10foot'); if(!foot) return;
  const base=ENDPOINT+'?action=download&run='+encodeURIComponent(RUN);
  foot.innerHTML =
    '<button class="btn" id="s10run">Buat Ulang</button>'+
    '<button class="btn btn-sec" id="dlExcel">Unduh Excel Utama</button>'+
    '<button class="btn btn-sec" id="dlLm">Unduh CSV LM</button>'+
    '<button class="btn btn-sec" id="dlPemb">Unduh CSV Pembaruan</button>';
  document.getElementById('s10run').onclick=runStep10;
  document.getElementById('dlExcel').onclick=()=>{ window.location = base+'&step=10'; };
  document.getElementById('dlLm').onclick=()=>{ window.location = base+'&part=lm'; };
  document.getElementById('dlPemb').onclick=()=>{ window.location = base+'&part=pembaruan'; };
}

function runStep10(){
  const btn=document.getElementById('s10run'); if(btn) btn.disabled=true;
  document.getElementById('msummary').classList.remove('show');
  const nama=(document.getElementById('f10nama')||{}).value||'';
  const fd=new FormData(); fd.append('penyusun', nama);
  setStatus('run','<span class="sp"></span>Menyusun laporan LM &amp; Pembaruan...');
  api('step10',{method:'POST', body:fd}).then(res=>{
    if(res && res.ok){
      STATE.steps[10]=res.artifact;
      setStatus('ok','\u2714 Laporan selesai. LM: '+(res.lm_rows||0)+' baris \u00b7 Pembaruan: '+(res.pembaruan_rows||0)+' baris.');
      showSummary(10, res.artifact);
      step10Downloads();
      renderRail();
    } else {
      if(btn) btn.disabled=false;
      setStatus('err','\u26A0 '+esc((res&&res.error)||'Gagal membuat laporan.'));
    }
  }).catch(e=>{ if(btn) btn.disabled=false; setStatus('err','\u26A0 '+esc(e.message||e)); });
}

/* ================= STEP 11: pembaruan intent (training phrase) ================= */
function openModal11(st){
  const modal=document.getElementById('modal');
  const ng=(STATE.ngrok_url)||'';
  modal.innerHTML =
    '<div class="mhead"><div class="mbadge">11</div>'+
      '<div><h2>'+esc(st.title)+'</h2><p>'+esc(st.sub)+'</p></div>'+
      '<button class="mx" id="mxBtn" title="Tutup">&times;</button></div>'+
    '<div class="mbody">'+
      '<div class="hint">Menyuntikkan training phrase baru (dari <b>Analisis Fallback</b> + <b>Analisis MKTA</b>, baris TINDAK LANJUT) ke file <b>usersays_&lt;lang&gt;.json</b> di dalam ZIP export Dialogflow. Diproses backend Python. Output: <b>Excel + sheet "Status Pembaruan"</b> dan <b>ZIP usersays terbaru</b> (file lain digabung utuh).</div>'+
      '<div class="field"><label>Bahasa target</label>'+
        '<select id="f11lang"><option value="id">usersays_id (Indonesia)</option><option value="en">usersays_en (English)</option></select></div>'+
      '<div class="field"><label>ZIP export Dialogflow</label><input type="file" id="f11zip" accept=".zip"><div class="hint">Berisi folder <code>intents/</code> dengan file <code>*_usersays_id/en.json</code>.</div></div>'+
      '<div class="field"><label>Sumber daftar frasa</label>'+
        '<div class="srcbox" data-group="s11">'+
          '<button type="button" class="srcbtn on" data-mode="auto">Dari step sebelumnya</button>'+
          '<button type="button" class="srcbtn" data-mode="manual">Upload workbook</button>'+
        '</div></div>'+
      '<div class="srcfield" data-group="s11" data-mode="auto"><div class="hint">Workbook diambil otomatis dari hasil Step 10 (atau Step 9): sheet Analisis Fallback &amp; Analisis MKTA.</div></div>'+
      '<div class="srcfield" data-group="s11" data-mode="manual"><div class="field"><label>Workbook pipeline (.xlsx)</label><input type="file" id="f11wb" accept=".xlsx"><div class="hint">Harus punya sheet Analisis Fallback &amp; Analisis MKTA.</div></div></div>'+
      '<div class="field"><label>Ngrok URL (opsional)</label><input type="text" id="f_ngrok" placeholder="kosongkan bila mode all-in-one Colab" value="'+esc(ng)+'"><div class="hint">Diabaikan bila backend dijalankan lokal di Colab (default).</div></div>'+
    '</div>'+
    '<div class="status" id="mstatus"></div>'+
    '<div class="summary" id="msummary"></div>'+
    '<div class="mfoot" id="s11foot">'+
      '<button class="btn" id="s11run">Perbarui usersays</button>'+
    '</div>';
  document.getElementById('overlay').classList.add('show');
  document.getElementById('mxBtn').onclick=closeModal;
  bindSourceToggle();
  const done=STATE.steps[11];
  if(done && done.summary && done.summary.bahasa){ const sel=document.getElementById('f11lang'); if(sel) sel.value=done.summary.bahasa; }
  document.getElementById('s11run').onclick=runStep11;
  if(done && done.status==='done'){ showSummary(11, done); step11Downloads(); }
}

function step11Downloads(){
  const foot=document.getElementById('s11foot'); if(!foot) return;
  const base=ENDPOINT+'?action=download&run='+encodeURIComponent(RUN);
  foot.innerHTML =
    '<button class="btn" id="s11run">Perbarui Ulang</button>'+
    '<button class="btn btn-sec" id="dlXls11">Unduh Excel (Status Pembaruan)</button>'+
    '<button class="btn btn-sec" id="dlZip11">Unduh ZIP usersays</button>';
  document.getElementById('s11run').onclick=runStep11;
  document.getElementById('dlXls11').onclick=()=>{ window.location = base+'&step=11'; };
  document.getElementById('dlZip11').onclick=()=>{ window.location = base+'&part=zip11'; };
}

function runStep11(){
  const fd=new FormData();
  try{
    const lang=val('f11lang')||'id';
    fd.append('lang', lang);
    const mode=currentMode('s11')||'auto';
    fd.append('mode', mode);
    const ng=val('f_ngrok'); if(ng){ STATE.ngrok_url=ng; fd.append('ngrok_url', ng); }
    const z=file('f11zip'); if(!z) throw 'Unggah ZIP export Dialogflow dulu.';
    fd.append('df_zip', z);
    if(mode==='manual'){ const w=file('f11wb'); if(!w) throw 'Opsi Upload workbook: pilih file workbook .xlsx (punya sheet Analisis).'; fd.append('workbook', w); }
  }catch(msg){ setStatus('err', esc(msg)); return; }
  const btn=document.getElementById('s11run'); if(btn) btn.disabled=true;
  const sm=document.getElementById('msummary'); if(sm) sm.classList.remove('show');
  setStatus('run','<span class="sp"></span>Mengirim ZIP + frasa ke backend & memperbarui usersays...');
  api('step11',{method:'POST', body:fd}).then(res=>{
    if(res && res.ok){
      STATE.steps[11]=res.artifact;
      const s=res.stats||{};
      setStatus('ok','\u2714 Selesai. Ditambahkan: '+(s.ditambahkan||0)+' \u00b7 Duplikat: '+(s.duplikat||0)+' \u00b7 Tidak ketemu: '+(s.intent_tidak_ketemu||0)+' \u00b7 File diperbarui: '+(s.file_diperbarui||0)+'.');
      showSummary(11, res.artifact);
      step11Downloads();
      renderRail();
    } else {
      if(btn) btn.disabled=false;
      setStatus('err','\u26A0 '+esc((res&&res.error)||'Gagal memperbarui usersays.'));
    }
  }).catch(e=>{ if(btn) btn.disabled=false; setStatus('err','\u26A0 '+esc(e.message||e)); });
}

function closeS6Menus(){ document.querySelectorAll('.s6menu.open').forEach(m=>m.classList.remove('open')); }

function openS6Menu(i){
  closeS6Menus();
  const r=STEP6.rows[i]; if(!r) return;
  const menu=document.getElementById('menu'+i); if(!menu) return;
  const opts=r.options||[];
  if(!opts.length){
    menu.innerHTML='<div class="s6opt s6empty">Tidak ada rekomendasi untuk baris ini</div>';
  } else {
    menu.innerHTML=opts.map((o,k)=>'<div class="s6opt" data-id="'+esc(o.id)+'"><div class="s6opt-id">'+(k+1)+'. '+esc(o.id)+'</div><div class="s6opt-meta">Skor '+esc(o.skor||'-')+' \u00b7 '+esc(o.conf||'-')+'</div></div>').join('');
    menu.querySelectorAll('.s6opt').forEach(el=>{ el.onmousedown=(e)=>{ e.preventDefault(); const id=el.getAttribute('data-id'); const inp=document.querySelector('.s6intent[data-i="'+i+'"]'); if(inp){ inp.value=id; } onIntentChange(i, id); closeS6Menus(); }; });
  }
  const inp=document.querySelector('.s6intent[data-i="'+i+'"]');
  if(inp){ const rect=inp.getBoundingClientRect(); menu.style.left=rect.left+'px'; menu.style.top=(rect.bottom+4)+'px'; menu.style.minWidth=Math.max(260, rect.width+40)+'px'; }
  menu.classList.add('open');
}

function saveStep6(){
  const edits=STEP6.rows.map(r=>({row:r.row, intent:r.intent||'', isi:r.isi||''}));
  const fd=new FormData(); fd.append('edits', JSON.stringify(edits));
  const btn=document.getElementById('s6save'); btn.disabled=true;
  setStatus('run','<span class="sp"></span>Menyimpan '+edits.length+' baris & membuat XLSX final...');
  api('step6',{method:'POST', body:fd}).then(res=>{
    btn.disabled=false;
    if(res && res.ok){ STATE.steps[6]=res.artifact; setStatus('ok','\u2714 Tersimpan. File final siap diunduh.'); showDone(6); renderRail(); }
    else { setStatus('err','\u26A0 '+esc((res&&res.error)||'Gagal menyimpan.')); }
  }).catch(e=>{ btn.disabled=false; setStatus('err','\u26A0 '+esc(e.message||e)); });
}

function val(id){ const el=document.getElementById(id); return el?el.value.trim():''; }
function file(id){ const el=document.getElementById(id); return (el&&el.files&&el.files[0])?el.files[0]:null; }

document.getElementById('resetBtn').onclick=()=>{
  if(!confirm('Reset run ini? Semua hasil step di server akan dihapus.')) return;
  api('reset',{}).then(()=>{
    localStorage.removeItem('dfp_run');
    RUN='run_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,7);
    localStorage.setItem('dfp_run',RUN);
    STATE={steps:{}}; renderRail();
  });
};

// Muat state awal
api('state',{}).then(res=>{
  if(res && res.steps) STATE.steps = res.steps;
  if(res && res.ngrok_url) STATE.ngrok_url = res.ngrok_url;
  renderRail();
}).catch(()=>renderRail());
renderRail();
</script>
</body>
</html>
PAGE;
}
