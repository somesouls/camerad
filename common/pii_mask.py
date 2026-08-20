# -*- coding: utf-8 -*-
"""Masking PII — Fase A (regex) + Fase B (Presidio, opsional).

Titik integrasi TERPUSAT: panggil mask_text(t) sebelum teks dikirim ke LLM.
Aktif default; matikan via env PII_MASKING=off/0/false/no.

Pemilihan mesin via env PII_ENGINE:
  - "regex" (default): pola regex Indonesia (NIK/NPWP/HP/email). Tanpa dependensi.
  - "presidio"/"auto": pakai Microsoft Presidio bila terpasang untuk deteksi
    tambahan NAMA (PERSON) & LOKASI (LOCATION) via NER + custom recognizer
    NIK/NPWP/HP. Bila Presidio/model tidak tersedia ATAU gagal, otomatis
    fallback ke regex (masking tidak pernah di-bypass).

Fase B butuh (opsional):
  pip install presidio-analyzer presidio-anonymizer
  python -m spacy download xx_ent_wiki_sm
Set PII_ENGINE=presidio dan (opsional) PII_SPACY_MODEL=xx_ent_wiki_sm.
"""
import os
import re

__all__ = ["mask_text", "mask", "masking_enabled", "scan"]


def masking_enabled():
    v = (os.environ.get("PII_MASKING", "on") or "").strip().lower()
    return v not in ("off", "0", "false", "no")


def _engine():
    return (os.environ.get("PII_ENGINE", "regex") or "regex").strip().lower()


# --- Pola PII Indonesia (Fase A) ---------------------------------------
_RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_RE_NPWP_FMT = re.compile(r"\b\d{2}\.\d{3}\.\d{3}\.\d-\d{3}\.\d{3}\b")
_RE_NIK16 = re.compile(r"(?<!\d)\d{16}(?!\d)")
_RE_NPWP15 = re.compile(r"(?<!\d)\d{15}(?!\d)")
_RE_HP = re.compile(r"(?<![\w+])(?:\+?62|0)8\d(?:[ .\-]?\d){7,11}(?!\w)")

_PIPELINE = [
    (_RE_NPWP_FMT, "<NPWP>"),
    (_RE_EMAIL, "<EMAIL>"),
    (_RE_NIK16, "<NIK>"),
    (_RE_NPWP15, "<NPWP>"),
    (_RE_HP, "<HP>"),
]


def _mask_regex(s):
    for rx, repl in _PIPELINE:
        s = rx.sub(repl, s)
    return s


# --- Presidio (Fase B, lazy + cached) ----------------------------------
_PRESIDIO = {"tried": False, "ok": False, "analyzer": None, "anonymizer": None, "lang": "en"}


def _init_presidio():
    if _PRESIDIO["tried"]:
        return _PRESIDIO["ok"]
    _PRESIDIO["tried"] = True
    try:
        from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_anonymizer import AnonymizerEngine

        model = (os.environ.get("PII_SPACY_MODEL", "xx_ent_wiki_sm") or "xx_ent_wiki_sm").strip()
        lang = "xx" if model.startswith("xx") else (model.split("_")[0] or "en")
        provider = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": lang, "model_name": model}],
        })
        nlp_engine = provider.create_engine()
        analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=[lang])

        def _pr(entity, patterns):
            pats = [Pattern(name=(entity + "_p%d" % i), regex=rx, score=0.85)
                    for i, rx in enumerate(patterns)]
            return PatternRecognizer(supported_entity=entity, patterns=pats,
                                     supported_language=lang)

        analyzer.registry.add_recognizer(_pr("ID_NIK", [r"(?<!\d)\d{16}(?!\d)"]))
        analyzer.registry.add_recognizer(_pr("ID_NPWP", [
            r"\b\d{2}\.\d{3}\.\d{3}\.\d-\d{3}\.\d{3}\b", r"(?<!\d)\d{15}(?!\d)"]))
        analyzer.registry.add_recognizer(_pr("ID_PHONE", [
            r"(?<![\w+])(?:\+?62|0)8\d(?:[ .\-]?\d){7,11}(?!\w)"]))

        _PRESIDIO.update({"analyzer": analyzer, "anonymizer": AnonymizerEngine(),
                          "lang": lang, "ok": True})
    except Exception:
        _PRESIDIO["ok"] = False
    return _PRESIDIO["ok"]


_LABELS = {
    "ID_NIK": "<NIK>", "ID_NPWP": "<NPWP>", "ID_PHONE": "<HP>",
    "PHONE_NUMBER": "<HP>", "EMAIL_ADDRESS": "<EMAIL>",
    "PERSON": "<NAMA>", "LOCATION": "<LOKASI>", "GPE": "<LOKASI>",
    "IP_ADDRESS": "<IP>",
}


def _mask_presidio(s):
    from presidio_anonymizer.entities import OperatorConfig
    a = _PRESIDIO["analyzer"]
    an = _PRESIDIO["anonymizer"]
    lang = _PRESIDIO.get("lang", "en")
    results = a.analyze(text=s, language=lang, entities=list(_LABELS.keys()))
    operators = {ent: OperatorConfig("replace", {"new_value": lab})
                 for ent, lab in _LABELS.items()}
    operators["DEFAULT"] = OperatorConfig("replace", {"new_value": "<PII>"})
    return an.anonymize(text=s, analyzer_results=results, operators=operators).text


def mask_text(text, enabled=None):
    if text is None or not isinstance(text, str) or not text:
        return text
    if enabled is None:
        enabled = masking_enabled()
    if not enabled:
        return text
    if _engine() in ("presidio", "auto"):
        if _init_presidio():
            try:
                # regex dulu (ID deterministik), lalu NER Presidio untuk nama/lokasi
                return _mask_presidio(_mask_regex(text))
            except Exception:
                return _mask_regex(text)
        return _mask_regex(text)
    return _mask_regex(text)


mask = mask_text


def scan(text):
    if not text or not isinstance(text, str):
        return {}
    return {
        "NPWP_fmt": len(_RE_NPWP_FMT.findall(text)),
        "EMAIL": len(_RE_EMAIL.findall(text)),
        "NIK16": len(_RE_NIK16.findall(text)),
        "NPWP15": len(_RE_NPWP15.findall(text)),
        "HP": len(_RE_HP.findall(text)),
    }
