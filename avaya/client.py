# -*- coding: utf-8 -*-
"""
avaya_client.py
---------------
Klien server-side untuk menarik percakapan Live-Chat dari Avaya WFO
(recapp) TANPA extension browser. Mereplikasi alur nyata yang ditangkap dari
DevTools (3 Agu 2026):

LOGIN = 2 LANGKAH form-POST ke /wfo/control/signin (dikonfirmasi):
  0) GET /wfo/control/signin        -> set cookie csrfp_login (+ JSESSIONID)
  1) POST username saja, csrfp_token=false, csrfp_login=<cookie>
                                    -> server set cookie csrfp_token
  2) POST username+password, csrfp_token=<cookie step1>, csrfp_login=<cookie>
                                    -> server set cookie Impact360AuthToken (+ JSESSIONID baru)
Setelah itu semua endpoint Ultra/Platform dipanggil ulang seperti extension.
Token CSRF berotasi tiap response -> selalu dibaca ulang dari cookie jar.

Header auth utk API (padanan yang ditangkap extension):
  Impact360AuthToken = cookie Impact360AuthToken
  X-CSRF-Header      = cookie csrfp_token
  X-CSRF-Login       = cookie csrfp_login

KREDENSIAL TIDAK DISIMPAN: username/password hanya dipakai saat login lalu
dilupakan.

Hanya butuh stdlib + `requests`. Lapisan HTTP bisa disuntik (parameter
`session=`) supaya bisa diuji offline tanpa jaringan.
"""
import os
import re
import json as _json
import time as _time
import datetime as _dt

BASE_DEFAULT = "https://recapp.intranet.pajak.go.id"
SIGNIN_PATH = "/wfo/control/signin"
LOGIN_PAGE_PATH = "/wfo/control/signin"
FORWARD_PATH = "/wfo/control/forward"

DATA_LIMIT = 2100
MIN_WINDOW_SEC = 120  # batas pecah rentang (2 menit), sama dgn extension

# field form login (dikonfirmasi dari tangkapan DevTools)
LOGIN_FORM_DEFAULTS = {
    "browserCheckEnabled": "true",
    "language": "en_US",
    "defaultHttpPort": "80",
    "screenHeight": "816",
    "screenWidth": "1536",
    "pageModelType": "0",
    "pageDirty": "false",
    "pageAction": "Login",
}

DEFAULT_COLS = [
    'audio_ch_num', 'audio_module_num', 'sid', 'site_id', 'personal_id',
    'transaction_id', 'audio_start_time', 'audio_start_time_gmt',
    'duration_seconds', 'personal_name', 'contact_actions', 'ani', 'call_id',
    'dnis', 'string_extension', 'sri', 'delete', 'media_type_bit_mask',
    'interaction_type_id', 'session_total_hold_time_in_seconds',
    'local_audio_start_time', 'uniquecallfield',
]

TAKSONOMI = [
    ('Lupa/Aktivasi EFIN', ['efin']),
    ('Coretax / Aktivasi Akun', ['coretax', 'aktivasi akun', 'akun wajib pajak', 'aktivasi wajib pajak', 'akun coretax']),
    ('Kode Otorisasi / Sertel', ['kode otorisasi', 'sertifikat elektronik', 'sertel', 'passphrase']),
    ('Pelaporan SPT', ['spt', 'e-filing', 'efiling', 'lapor pajak', 'pelaporan', 'e-form', 'eform']),
    ('Perubahan Data', ['perubahan data', 'pemutakhiran', 'ubah data', 'ganti email', 'ubah email', 'pindah kpp', 'ganti nomor', 'ubah nomor']),
    ('Konfirmasi/Validasi NPWP-NIK', ['konfirmasi npwp', 'validasi nik', 'pemadanan', 'padankan', 'nik npwp', 'npwp nik', 'valid nik']),
    ('Daftar NPWP', ['daftar npwp', 'pendaftaran npwp', 'buat npwp', 'registrasi npwp', 'pembuatan npwp']),
    ('Aktivasi/Nonaktif NPWP', ['non efektif', 'non-efektif', 'penonaktifan', 'mengaktifkan kembali', 'npwp ne', 'wp ne', 'status ne']),
    ('Cetak NPWP', ['cetak npwp', 'kartu npwp', 'cetak ulang npwp']),
    ('Faktur Pajak', ['faktur', 'e-faktur', 'efaktur']),
    ('Billing/Pembayaran', ['billing', 'kode billing', 'e-billing', 'ntpn', 'bayar pajak', 'pembayaran pajak', 'setor pajak', 'id billing']),
    ('Bukti Potong/Bupot', ['bukti potong', 'bupot', 'ebupot', 'e-bupot']),
    ('Kode Error Coretax', ['kode error', 'error coretax', 'gagal login', 'tidak bisa login', 'tidak bisa masuk']),
    ('PPh/PPN Umum', ['pph', 'ppn', 'pajak penghasilan', 'pajak pertambahan']),
    ('PP 20/2026', ['pp 20', 'pp nomor 20', 'pp no 20']),
]


class AvayaAuthError(Exception):
    pass


class AvayaPullError(Exception):
    pass


# ------------------------------------------------------------------ helpers
def _env_bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() not in ("", "0", "false", "no", "off")


def _strip_nik(s):
    s = str(s or "")
    i = s.find("[")
    return (s[:i] if i >= 0 else s).strip()


def _extract_nik(s):
    s = str(s or "")
    a, b = s.find("["), s.find("]")
    return s[a + 1:b].strip() if (a >= 0 and b > a) else ""


def _all_zeros(s):
    return bool(s) and all(ch == "0" for ch in s)


def _all_digits(s):
    return bool(s) and s.isdigit()


def _is_bot_name(nm):
    nm = str(nm or "").lower()
    return ("ccai" in nm or "chatbot" in nm or "virtual assistant" in nm or "google" in nm)


def _utt_text(u):
    if not isinstance(u, dict):
        return ""
    t = ""
    for k in ("Text", "text", "Body", "body", "Content", "content", "Message", "message"):
        if u.get(k):
            t = u.get(k)
            break
    if not t and isinstance(u.get("Segments"), list):
        t = " ".join(str(s.get("Text") or s.get("text") or "") for s in u["Segments"] if isinstance(s, dict))
    t = re.sub(r"<[^>]+>", " ", str(t or ""))
    return re.sub(r"\s+", " ", t).strip()


def _detect_topic(t):
    t = str(t or "").lower()
    for topik, keys in TAKSONOMI:
        for k in keys:
            if k in t:
                return topik
    return "Lainnya"


def _fmt_local(dtobj):
    return dtobj.strftime("%Y-%m-%dT%H:%M:%S")


def _parse_local(s):
    return _dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")


def _today_str():
    return _dt.datetime.now().strftime("%Y-%m-%d")


def _safe_json(r):
    try:
        return r.json()
    except Exception:
        return None


def _resp_text(r):
    try:
        return r.text or ""
    except Exception:
        return ""


# ================================================================= client
class AvayaClient:
    def __init__(self, base_url=None, verify_ssl=None, timeout=60, session=None):
        self.base = (base_url or os.environ.get("AVAYA_BASE_URL") or BASE_DEFAULT).rstrip("/")
        if verify_ssl is None:
            # intranet pakai sertifikat internal + tangkapan pakai --insecure ->
            # default TIDAK verifikasi; bisa dipaksa via env AVAYA_VERIFY_SSL=1
            verify_ssl = _env_bool("AVAYA_VERIFY_SSL", False)
        self.verify = verify_ssl
        self.timeout = timeout
        self._auth = {"x-requested-with": "XMLHttpRequest", "accept": "*/*"}
        self._logged_in = False
        self._form_id = None
        if session is not None:
            self.session = session
        else:
            import requests  # ditunda supaya import modul tak wajib ada requests
            self.session = requests.Session()
            self.session.verify = self.verify
            if not self.verify:
                # recapp intranet memakai sertifikat internal, jadi verify=False
                # (padanan klik "Lanjutkan" di halaman "not secure" browser).
                # requests/urllib3 mencetak InsecureRequestWarning pada SETIAP
                # request; satu kali tarik (ratusan request) bikin log membanjir.
                # Redam HANYA peringatan spesifik ini. Jika AVAYA_VERIFY_SSL
                # diaktifkan, peringatan tetap tampil sebagaimana mestinya.
                try:
                    import urllib3
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                except Exception:
                    pass
            self.session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/150.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            })

    # ---------------- HTTP ----------------
    def _u(self, path):
        return self.base + path if path.startswith("/") else self.base + "/" + path

    def _get(self, path, headers=None):
        return self.session.get(self._u(path), headers=headers or {}, timeout=self.timeout)

    def _post(self, path, headers=None, data=None):
        return self.session.post(self._u(path), headers=headers or {}, data=data, timeout=self.timeout)

    def _cookies_dict(self):
        try:
            return {c.name: c.value for c in self.session.cookies}
        except Exception:
            try:
                return dict(self.session.cookies)
            except Exception:
                return {}

    def _cookie(self, name, default=""):
        return self._cookies_dict().get(name, default)

    def _refresh_auth(self):
        """Baca ulang cookie (token CSRF berotasi tiap response) ke header auth."""
        ck = self._cookies_dict()
        if ck.get("Impact360AuthToken"):
            self._auth["Impact360AuthToken"] = ck["Impact360AuthToken"]
        if ck.get("csrfp_token"):
            self._auth["X-CSRF-Header"] = ck["csrfp_token"]
        if ck.get("csrfp_login"):
            self._auth["X-CSRF-Login"] = ck["csrfp_login"]

    # ---------------- login (2 langkah) ----------------
    def login(self, username, password):
        """Login 2-langkah ke Avaya WFO. Kredensial TIDAK disimpan.

        Mengembalikan True bila cookie Impact360AuthToken berhasil didapat;
        melempar AvayaAuthError bila gagal.
        """
        if not password:
            raise AvayaAuthError("Password wajib diisi.")
        origin = self.base
        ref = self._u(LOGIN_PAGE_PATH)
        form_headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": origin,
            "Referer": ref,
            "Upgrade-Insecure-Requests": "1",
        }
        # 0) GET halaman login -> set cookie csrfp_login + JSESSIONID
        try:
            self._get(LOGIN_PAGE_PATH, headers={"Referer": ref})
        except Exception as e:
            raise AvayaAuthError("Tidak bisa membuka halaman login: %r" % e)
        csrfp_login = self._cookie("csrfp_login")

        # 1) STEP 1: kirim username saja (csrfp_token=false)
        step1 = dict(LOGIN_FORM_DEFAULTS)
        step1["username"] = username or ""
        step1["csrfp_token"] = "false"
        step1["csrfp_login"] = csrfp_login
        try:
            r1 = self._post(SIGNIN_PATH, headers=form_headers, data=step1)
        except Exception as e:
            raise AvayaAuthError("Gagal mengirim username (langkah 1): %r" % e)
        if getattr(r1, "status_code", 0) in (401, 403):
            raise AvayaAuthError(
                "Langkah 1 ditolak (HTTP %s). Kemungkinan token CSRF login "
                "(csrfp_login) tidak valid/kedaluwarsa." % r1.status_code)
        # server set cookie csrfp_token utk langkah 2 (dan mungkin refresh csrfp_login)
        csrfp_token = self._cookie("csrfp_token")
        csrfp_login = self._cookie("csrfp_login") or csrfp_login

        # 2) STEP 2: kirim username + password + token CSRF
        step2 = dict(LOGIN_FORM_DEFAULTS)
        step2["username"] = username or ""
        step2["password"] = password  # requests akan meng-encode % dsb.
        step2["csrfp_token"] = csrfp_token or "false"
        step2["csrfp_login"] = csrfp_login
        try:
            r2 = self._post(SIGNIN_PATH, headers=form_headers, data=step2)
        except Exception as e:
            raise AvayaAuthError("Gagal mengirim sandi (langkah 2): %r" % e)
        status2 = getattr(r2, "status_code", 0)
        if status2 == 403:
            raise AvayaAuthError(
                "Login ditolak (HTTP 403) pada langkah 2 — token CSRF "
                "(csrfp_token) tidak cocok/kedaluwarsa. Coba lagi.")
        if status2 == 401:
            raise AvayaAuthError("Login ditolak (HTTP 401) — periksa username/sandi.")

        # 3) cookie Impact360AuthToken menandakan sukses
        token = self._cookie("Impact360AuthToken")
        if not token:
            # kadang di-set saat GET /forward pasca-login
            try:
                self._get(FORWARD_PATH + "?language=en_US&defaultHttpPort=80",
                          headers={"Referer": ref})
            except Exception:
                pass
            token = self._cookie("Impact360AuthToken")
        if not token:
            body = _resp_text(r2).lower()
            hint = ""
            if "invalid" in body and ("password" in body or "credential" in body or "user" in body):
                hint = " Server menyebut kredensial tidak valid — periksa username/sandi."
            raise AvayaAuthError(
                "Login tampak gagal: cookie Impact360AuthToken tidak muncul "
                "setelah 2 langkah." + hint)
        self._refresh_auth()
        self._logged_in = True
        # username/password sengaja TIDAK disimpan
        return True

    def _headers_get(self):
        self._refresh_auth()
        return dict(self._auth)

    def _headers_post(self):
        self._refresh_auth()
        h = dict(self._auth)
        h["content-type"] = "application/json"
        return h

    # ---------------- search payload ----------------
    def build_search_body(self, frm, to, sid):
        return {
            "RootElements": [
                {"Id": "SearchType", "Params": {"Type": "Interactions"}},
                {"Id": "InteractionTypes", "Params": {"Calls": True, "Emails": True, "Chats": True}},
            ],
            "Sections": [
                {"Id": "Employees", "Categories": [{"Id": "Employees", "Elements": [
                    {"Id": "Agents", "Params": {"Agents": [], "Groups": []}}]}]},
                {"Id": "DateRange", "Categories": [{"Id": "DateRange", "Elements": [
                    {"Id": "DateRangeCalls", "Params": {"Type": "Between", "To": to, "From": frm}},
                    {"Id": "UseLocalTime", "Params": {"Value": True}}]}]},
                {"Id": "Interactions", "Categories": [{"Id": "Interactions", "Elements": [
                    {"Id": "InteractionType", "Params": {"Values": [{"Id": "10", "Text": "Chat"}]}}]}]},
                {"Id": "Fts", "Categories": [{"Id": "Fts", "Elements": [
                    {"Id": "FTSLanguage", "Params": {"Value": "en"}}]}]},
            ],
            "Name": None, "ContextType": None, "Type": "QMSearch", "Id": sid, "Title": "advanced_search",
        }

    def _new_uuid(self):
        import uuid
        return str(uuid.uuid4())

    def create_search(self, frm, to):
        path_id = self._form_id or self._new_uuid()
        body = self.build_search_body(frm, to, path_id)
        url = "/Ultra/api/SearchServices/UserForm/%s?_dc=%d&type=Recent" % (path_id, int(_time.time() * 1000))
        r = self._post(url, headers=self._headers_post(), data=_json.dumps(body))
        j = _safe_json(r)
        if isinstance(j, dict) and j.get("Id"):
            self._form_id = j["Id"]
            return j["Id"]
        raise AvayaPullError("createSearch gagal (tidak ada Id). HTTP %s" % getattr(r, "status_code", "?"))

    def exec_search(self, sid):
        url = "/Ultra/api/SearchServices/UserForm/%s?_dc=%d" % (sid, int(_time.time() * 1000))
        try:
            return _safe_json(self._get(url, headers=self._headers_get()))
        except Exception:
            return None

    def get_header(self, sid):
        url = "/Ultra/api/QueryResults/Header/%s?_dc=%d&resultsMode=Search" % (sid, int(_time.time() * 1000))
        r = self._get(url, headers=self._headers_get())
        if not getattr(r, "ok", False):
            return {"header": [], "count": 0, "maxExceeded": False}
        j = _safe_json(r) or {}
        return {
            "header": j.get("Header", []) or [],
            "count": int(j.get("RowsCount", 0) or 0),
            "maxExceeded": j.get("IsMaxNumExceeded") in (True, "true"),
        }

    def get_data(self, sid):
        url = ("/Ultra/api/QueryResults/Data/%s?_dc=%d&page=1&start=0&limit=%d"
               "&sort=audio_start_time&dir=ASC" % (sid, int(_time.time() * 1000), DATA_LIMIT))
        r = self._get(url, headers=self._headers_get())
        if not getattr(r, "ok", False):
            raise AvayaPullError("Data HTTP %s — %s" % (getattr(r, "status_code", "?"), _resp_text(r)[:200]))
        j = _safe_json(r) or {}
        return j.get("Rows") or j.get("rows") or []

    def get_interaction(self, sid, dbsid):
        url = "/Platform/TextServices/Getinteraction?sid=%s&dbsid=%s" % (sid, dbsid)
        r = self._get(url, headers=self._headers_get())
        if not getattr(r, "ok", False):
            raise AvayaPullError("Getinteraction HTTP %s" % getattr(r, "status_code", "?"))
        d = _safe_json(r) or {}
        return d.get("Interaction") if isinstance(d, dict) else None

    # ---------------- row mapping ----------------
    def col_map(self, header):
        m = {}
        if header:
            for i, h in enumerate(header):
                if isinstance(h, dict) and "DataIndex" in h:
                    m[h["DataIndex"]] = i
        else:
            for j, c in enumerate(DEFAULT_COLS):
                m[c] = j
        return m

    @staticmethod
    def _cell(row, cm, key, field="Text"):
        idx = cm.get(key)
        if idx is None or idx >= len(row):
            return ""
        c = row[idx]
        if not isinstance(c, dict):
            return ""
        if field == "Date":
            return c.get("Date") or c.get("Text") or ""
        if field == "ItemId":
            return c.get("ItemId") or ""
        return c.get("Text") or ""

    def row_to_rec(self, row, cm):
        _c = self._cell
        try:
            durasi = int(_c(row, cm, "duration_seconds", "ItemId") or 0)
        except Exception:
            durasi = 0
        return {
            "sid": str(_c(row, cm, "sid")).strip(),
            "dbsid": str(_c(row, cm, "site_id")).strip(),
            "agentId": str(_c(row, cm, "personal_id")).strip(),
            "agentName": str(_c(row, cm, "personal_name")).strip(),
            "start": _c(row, cm, "audio_start_time", "Date"),
            "startGmt": _c(row, cm, "audio_start_time_gmt", "Date"),
            "durasi": durasi,
            "ani": str(_c(row, cm, "ani")).strip(),
            "customerRaw": str(_c(row, cm, "dnis")).strip(),
            "itype": str(_c(row, cm, "interaction_type_id")).strip(),
        }

    # ---------------- role & conv build ----------------
    def _role_of(self, u, bot_ids, id_to_name):
        sp = str(u.get("Meta_s_speaker") or u.get("Speaker") or u.get("speaker") or "").lower()
        if sp in ("customer", "external"):
            return "customer"
        _id = u.get("agentId")
        if _id is None:
            _id = u.get("SpeakerId")
        if _id is None:
            _id = u.get("EmployeeId")
        _id = str(_id if _id is not None else "")
        if _id and bot_ids.get(_id):
            return "bot"
        if _is_bot_name(id_to_name.get(_id, "")):
            return "bot"
        t = _utt_text(u).lower()
        if "virtual assistant (chat bot)" in t or "petugas kami akan segera membantu" in t:
            return "bot"
        if sp == "customer":
            return "customer"
        if sp == "agent":
            return "agent"
        return "agent"

    def build_conv(self, rec, inter):
        inter = inter or {}
        ids = inter.get("Meta_ss_employeeIDs") or inter.get("EmployeeIDs") or []
        names = inter.get("Meta_ss_employeesNames") or inter.get("EmployeeNames") or []
        id_to_name, bot_ids = {}, {}
        for i in range(len(ids)):
            nm = names[i] if i < len(names) else ""
            id_to_name[str(ids[i])] = nm
            if _is_bot_name(nm):
                bot_ids[str(ids[i])] = True
        agent_name = rec.get("agentName") or ""
        for nm in names:
            if not _is_bot_name(nm):
                agent_name = nm
                break
        cust_meta = (inter.get("Meta_ss_customerNames") or inter.get("CustomerNames") or [""])
        cust_meta = cust_meta[0] if cust_meta else ""
        cust_source = cust_meta or rec.get("customerRaw") or ""
        customer = _strip_nik(cust_source)
        nik = _extract_nik(cust_source) or _extract_nik(rec.get("customerRaw") or "")
        non_npwp = (nik == "" or _all_zeros(nik) or not _all_digits(nik))

        raw_utts = inter.get("Utterances") or inter.get("utterances") or inter.get("Messages") or []
        utts = []
        for u in raw_utts:
            if not isinstance(u, dict):
                continue
            role = self._role_of(u, bot_ids, id_to_name)
            text = _utt_text(u)
            if not text:
                continue
            utts.append({
                "role": role,
                "name": (customer or "CUSTOMER") if role == "customer" else ("CCAI" if role == "bot" else (agent_name or "AGENT")),
                "date": u.get("Date") or u.get("date") or u.get("Time") or "",
                "text": text,
            })
        n_bot = sum(1 for x in utts if x["role"] == "bot")
        n_agent = sum(1 for x in utts if x["role"] == "agent")
        n_cust = sum(1 for x in utts if x["role"] == "customer")
        cust_text = "\n".join(x["text"] for x in utts if x["role"] == "customer")
        agent_text = "\n".join(x["text"] for x in utts if x["role"] == "agent")
        topik = _detect_topic(cust_text + "\n" + agent_text)
        reached_agent = n_agent > 0 and bool(agent_name) and not _is_bot_name(agent_name)

        cust_utts = [x for x in utts if x["role"] == "customer"]
        first_cust = (cust_utts[0]["text"].strip() if cust_utts else "")
        immediate = "1500200" in first_cust
        first_q = ""
        for x in cust_utts:
            tx = (x["text"] or "").strip()
            if tx and "1500200" not in tx and len(tx) > 3:
                first_q = tx[:240]
                break
        deflection = reached_agent and topik != "Lainnya"
        tanggal = str(rec.get("start") or "")[:10] or _today_str()

        return {
            "sid": rec.get("sid"),
            "tanggal": tanggal,
            "start": rec.get("start") or "",
            "agentId": rec.get("agentId") or "",
            "agentName": agent_name,
            "agent": agent_name,
            "customer": customer,
            "nik": nik,
            "nonNpwp": non_npwp,
            "durasi": rec.get("durasi") or 0,
            "nBot": n_bot, "nAgent": n_agent, "nCust": n_cust,
            "topik": topik,
            "reachedAgent": reached_agent,
            "sampaiAgent": reached_agent,
            "immediate": immediate,
            "deflection": deflection,
            "firstQ": first_q,
            "transcript": utts,
            "transkrip": utts,
        }

    # ---------------- dedup (leg chatbot vs agent) ----------------
    @staticmethod
    def _sig_of(r):
        return "|".join([
            str(r.get("customerRaw") or "").strip().lower(),
            str(r.get("ani") or "").strip(),
            str(r.get("start") or "").strip(),
            str(r.get("durasi") or 0),
        ])

    def _dedup_recs(self, recs):
        by_sig, order = {}, []
        for r in recs:
            sig = self._sig_of(r)
            if sig not in by_sig:
                by_sig[sig] = r
                order.append(sig)
                continue
            if _is_bot_name(by_sig[sig].get("agentName")) and not _is_bot_name(r.get("agentName")):
                by_sig[sig] = r
        return [by_sig[s] for s in order]

    # ---------------- collector (auto-batch) ----------------
    def collect_window(self, frm, to, acc, on_prog=None, should_stop=None):
        if should_stop and should_stop():
            return
        sid = self.create_search(frm, to)
        self.exec_search(sid)
        info = self.get_header(sid)
        rows = self.get_data(sid)
        capped = info["maxExceeded"] or len(rows) >= 2000
        if on_prog:
            on_prog("Rentang %s .. %s -> %d%s" % (frm[:16], to[11:16], len(rows) or info["count"],
                                                   " (terpotong, dipecah)" if capped else ""))
        if capped and (_parse_local(to) - _parse_local(frm)).total_seconds() > MIN_WINDOW_SEC:
            mid = _parse_local(frm) + (_parse_local(to) - _parse_local(frm)) / 2
            self.collect_window(frm, _fmt_local(mid), acc, on_prog, should_stop)
            self.collect_window(_fmt_local(mid + _dt.timedelta(seconds=1)), to, acc, on_prog, should_stop)
            return
        cm = self.col_map(info["header"])
        for row in rows:
            rec = self.row_to_rec(row, cm)
            if rec["sid"]:
                acc[rec["sid"]] = rec

    # ---------------- public: pull_range ----------------
    def pull_range(self, day_from, day_to, on_prog=None, should_stop=None, fetch_transcript=True):
        """Tarik semua percakapan Chat pada rentang tanggal (inklusif).

        day_from/day_to: 'YYYY-MM-DD'. Mengembalikan list objek percakapan
        (berbentuk sama dgn ekspor extension) siap dianalisis run_pipeline.
        """
        if not self._logged_in:
            raise AvayaAuthError("Belum login.")
        frm = str(day_from)[:10] + "T00:00:00"
        to = str(day_to)[:10] + "T23:59:59"
        acc = {}
        if on_prog:
            on_prog("Mengumpulkan daftar interaksi %s .. %s" % (day_from, day_to))
        self.collect_window(frm, to, acc, on_prog, should_stop)
        recs = self._dedup_recs(list(acc.values()))
        if on_prog:
            on_prog("Ditemukan %d interaksi unik. Mengambil transkrip…" % len(recs))
        convs = []
        for i, rec in enumerate(recs):
            if should_stop and should_stop():
                break
            inter = None
            if fetch_transcript:
                try:
                    inter = self.get_interaction(rec["sid"], rec["dbsid"])
                except AvayaPullError:
                    inter = None
            convs.append(self.build_conv(rec, inter))
            if on_prog and (i + 1) % 25 == 0:
                on_prog("Transkrip %d/%d" % (i + 1, len(recs)))
        if on_prog:
            on_prog("Selesai menarik %d percakapan." % len(convs))
        return convs


# ===================================================================
# SMOKE TEST OFFLINE (tanpa jaringan) — transport disuntik (FakeSession)
# Mensimulasikan alur login 2-langkah + rotasi cookie csrfp_token.
# ===================================================================
if __name__ == "__main__":
    class _Resp:
        def __init__(self, status=200, js=None, text=""):
            self.status_code = status
            self.ok = 200 <= status < 400
            self._js = js
            self.text = text
            self.headers = {}

        def json(self):
            if self._js is None:
                raise ValueError("no json")
            return self._js

    class _Cookies(dict):
        def __iter__(self):
            for k, v in list(self.items()):
                yield type("C", (), {"name": k, "value": v})

    class FakeSession:
        """Simulasi login 2-langkah + endpoint pull Avaya."""
        def __init__(self):
            self.cookies = _Cookies()
            self.headers = {}
            self.verify = True
            self._search_id = "SEARCH-1"
            self._login_step = 0

        def get(self, url, headers=None, timeout=None):
            if url.endswith("/control/signin"):
                # GET halaman login -> set csrfp_login + JSESSIONID
                self.cookies["csrfp_login"] = "LOGINCSRF"
                self.cookies["JSESSIONID"] = "JSESS-INIT"
                return _Resp(200, text="<html>login oformmain</html>")
            if "/control/forward" in url:
                return _Resp(200, text="dashboard")
            if "/SearchServices/UserForm/" in url:
                return _Resp(200, js={"Id": self._search_id})
            if "/QueryResults/Header/" in url:
                header = [{"DataIndex": c} for c in DEFAULT_COLS]
                return _Resp(200, js={"Header": header, "RowsCount": 1, "IsMaxNumExceeded": False})
            if "/QueryResults/Data/" in url:
                cells = [{"Text": ""} for _ in DEFAULT_COLS]
                idx = {c: i for i, c in enumerate(DEFAULT_COLS)}
                cells[idx["sid"]] = {"Text": "S1"}
                cells[idx["site_id"]] = {"Text": "DB1"}
                cells[idx["personal_id"]] = {"Text": "A99"}
                cells[idx["personal_name"]] = {"Text": "Petugas Andi"}
                cells[idx["audio_start_time"]] = {"Date": "2026-07-10T09:15:00", "Text": "2026-07-10T09:15:00"}
                cells[idx["duration_seconds"]] = {"ItemId": "120"}
                cells[idx["dnis"]] = {"Text": "Budi[3210000000000001]"}
                cells[idx["interaction_type_id"]] = {"Text": "10"}
                return _Resp(200, js={"Rows": [cells]})
            if "/TextServices/Getinteraction" in url:
                return _Resp(200, js={"Interaction": {
                    "Meta_ss_employeeIDs": ["BOT1", "A99"],
                    "Meta_ss_employeesNames": ["CCAI Virtual Assistant", "Petugas Andi"],
                    "Meta_ss_customerNames": ["Budi[3210000000000001]"],
                    "Utterances": [
                        {"Meta_s_speaker": "customer", "Text": "saya mau lapor SPT tahunan"},
                        {"agentId": "BOT1", "Text": "Virtual Assistant (chat bot) siap membantu"},
                        {"agentId": "A99", "Text": "Baik pak, saya bantu proses SPT"},
                    ],
                }})
            return _Resp(404, text="nf")

        def post(self, url, headers=None, data=None, timeout=None):
            data = data or {}
            if url.endswith("/control/signin"):
                has_pw = bool(data.get("password"))
                if not has_pw:
                    # LANGKAH 1: harus bawa csrfp_login + csrfp_token=false
                    assert data.get("csrfp_login") == "LOGINCSRF", data
                    assert data.get("csrfp_token") == "false", data
                    self.cookies["csrfp_token"] = "TOK-STEP1"  # server set token utk step 2
                    return _Resp(200, text="password page")
                # LANGKAH 2: harus bawa token dari step 1
                assert data.get("csrfp_token") == "TOK-STEP1", data
                assert data.get("password") == "Rahasia%", data  # % harus lolos apa adanya
                self.cookies["Impact360AuthToken"] = "TOKEN-XYZ"
                self.cookies["JSESSIONID"] = "JSESS-AUTHED"
                self.cookies["csrfp_token"] = "TOK-ROTATED"  # rotasi
                return _Resp(200, text="OK dashboard")
            if "/SearchServices/UserForm/" in url:
                return _Resp(200, js={"Id": self._search_id})
            return _Resp(404, text="nf")

    fs = FakeSession()
    c = AvayaClient(base_url="https://x", session=fs)
    assert c.login("817930954", "Rahasia%") is True, "login gagal"
    assert c._auth.get("Impact360AuthToken") == "TOKEN-XYZ", c._auth
    assert c._auth.get("X-CSRF-Header") == "TOK-ROTATED", c._auth
    assert c._auth.get("X-CSRF-Login") == "LOGINCSRF", c._auth
    logs = []
    convs = c.pull_range("2026-07-10", "2026-07-10", on_prog=logs.append)
    assert len(convs) == 1, convs
    cv = convs[0]
    assert cv["sid"] == "S1", cv
    assert cv["customer"] == "Budi", cv
    assert cv["nik"] == "3210000000000001", cv
    assert cv["topik"] == "Pelaporan SPT", cv
    roles = [u["role"] for u in cv["transcript"]]
    assert roles == ["customer", "bot", "agent"], roles
    assert cv["reachedAgent"] is True and cv["nBot"] == 1 and cv["nAgent"] == 1, cv
    assert cv["durasi"] == 120, cv
    body = c.build_search_body("2026-07-10T00:00:00", "2026-07-10T23:59:59", "ID1")
    assert body["Type"] == "QMSearch"
    assert body["Sections"][2]["Categories"][0]["Elements"][0]["Params"]["Values"][0]["Id"] == "10"
    print("AVAYA_CLIENT_SMOKE_OK")
