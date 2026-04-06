"""Jurisdiction risk assessment for GDPR data transfers."""
from __future__ import annotations

from typing import Literal

# EU member states (27)
_EU = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
    "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
    "PL", "PT", "RO", "SK", "SI", "ES", "SE",
}

# EEA (non-EU)
_EEA = {"NO", "IS", "LI"}

# Countries with EU adequacy decisions
_ADEQUACY = {
    "GB", "JP", "KR", "NZ", "CH", "CA", "IL", "UY", "AR",
    "AD", "FO", "GG", "IM", "JE",
    "US",  # EU-US Data Privacy Framework (DPF) — adequacy decision July 2023
}

ADEQUATE_COUNTRIES: set[str] = _EU | _EEA | _ADEQUACY

_SAFEGUARD_BASES = {"SCCs", "BCRs", "consent", "adequacy_decision"}


# Mapping of country-code TLDs to ISO 3166-1 alpha-2 codes (~40 common ones)
_CCTLD_MAP: dict[str, str] = {
    "ac": "SH", "ad": "AD", "ae": "AE", "af": "AF", "ag": "AG",
    "al": "AL", "am": "AM", "ao": "AO", "ar": "AR", "at": "AT",
    "au": "AU", "az": "AZ", "ba": "BA", "bd": "BD", "be": "BE",
    "bg": "BG", "bh": "BH", "br": "BR", "by": "BY", "ca": "CA",
    "ch": "CH", "cl": "CL", "cn": "CN", "co": "CO", "cr": "CR",
    "cy": "CY", "cz": "CZ", "de": "DE", "dk": "DK", "dz": "DZ",
    "ec": "EC", "ee": "EE", "eg": "EG", "es": "ES", "et": "ET",
    "fi": "FI", "fr": "FR", "gb": "GB", "ge": "GE", "gh": "GH",
    "gr": "GR", "gt": "GT", "hk": "HK", "hr": "HR", "hu": "HU",
    "id": "ID", "ie": "IE", "il": "IL", "in": "IN", "iq": "IQ",
    "ir": "IR", "is": "IS", "it": "IT", "jo": "JO", "jp": "JP",
    "ke": "KE", "kg": "KG", "kh": "KH", "kr": "KR", "kw": "KW",
    "kz": "KZ", "lb": "LB", "li": "LI", "lk": "LK", "lt": "LT",
    "lu": "LU", "lv": "LV", "ly": "LY", "ma": "MA", "me": "ME",
    "mk": "MK", "mm": "MM", "mn": "MN", "mt": "MT", "mx": "MX",
    "my": "MY", "ng": "NG", "nl": "NL", "no": "NO", "np": "NP",
    "nz": "NZ", "om": "OM", "pa": "PA", "pe": "PE", "ph": "PH",
    "pk": "PK", "pl": "PL", "pt": "PT", "py": "PY", "qa": "QA",
    "ro": "RO", "rs": "RS", "ru": "RU", "sa": "SA", "se": "SE",
    "sg": "SG", "si": "SI", "sk": "SK", "th": "TH", "tn": "TN",
    "tr": "TR", "tw": "TW", "ua": "UA", "uk": "GB", "uy": "UY",
    "uz": "UZ", "ve": "VE", "vn": "VN", "za": "ZA", "zw": "ZW",
}

# TLDs that look like country codes but are generic — do NOT infer a country
_GENERIC_TLDS: set[str] = {
    "com", "org", "net", "io", "ai", "co", "dev", "app", "tech", "xyz",
    "info", "biz", "gov", "edu", "mil", "int", "arpa", "mobi", "name",
}


def infer_country_code(domain: str) -> str:
    """Infer ISO country code from domain TLD.

    Returns "" for empty domain, generic TLDs, or unknown ccTLDs.
    Handles two-part TLDs like .co.uk and .co.jp.
    """
    if not domain:
        return ""
    parts = domain.lower().rstrip(".").split(".")
    if len(parts) < 2:
        return ""
    # Check for two-part TLD (e.g. co.uk, co.jp, com.au)
    if len(parts) >= 3 and parts[-2] in _GENERIC_TLDS:
        tld = parts[-1]
        if tld not in _GENERIC_TLDS:
            return _CCTLD_MAP.get(tld, "")
    tld = parts[-1]
    if tld in _GENERIC_TLDS:
        return ""
    return _CCTLD_MAP.get(tld, "")


def assess_risk(
    country_code: str | None, transfer_basis: str
) -> Literal["adequate", "safeguarded", "risky", "unknown"]:
    """Assess transfer risk based on jurisdiction and legal basis."""
    if not country_code:
        return "unknown"
    if country_code in ADEQUATE_COUNTRIES:
        return "adequate"
    if transfer_basis in _SAFEGUARD_BASES:
        return "safeguarded"
    return "risky"
