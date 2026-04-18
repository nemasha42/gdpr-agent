"""Classify Gmail reply messages into tags using regex + optional LLM fallback.

Three-pass strategy:
  Pass 0 — NON_GDPR pre-pass: detect newsletters/marketing and short-circuit
  Pass 1 — regex on snippet / subject / from headers (free, fast)
  Pass 2 — LLM (Claude Haiku) only when Pass 1 produces no useful tags
"""

from __future__ import annotations

import json
import re
from typing import Any

from reply_monitor.models import ClassificationResult

# ---------------------------------------------------------------------------
# Compiled regex patterns per tag
# Tag fires if ANY of its patterns matches the corresponding field.
# ---------------------------------------------------------------------------

# (tag, [(field, pattern), ...])  — field: "from" | "subject" | "snippet" | "text"
# "text" = subject + " " + snippet combined
_RULES: list[tuple[str, list[tuple[str, re.Pattern]]]] = [
    ("BOUNCE_PERMANENT", [
        ("from",    re.compile(r"mailer-daemon@|postmaster@", re.I)),
        ("subject", re.compile(r"delivery status notification|undeliverable|mail delivery|failure notice|returned mail", re.I)),
        ("snippet", re.compile(
            r"550[\s.]|5\.1\.1|email account does not exist"
            r"|group you tried to contact|may not exist or you may not have permission"
            r"|address not found|no such user",
            re.I)),
    ]),
    ("BOUNCE_TEMPORARY", [
        # Distinguished from BOUNCE_PERMANENT by snippet content: 4xx codes or transient signals
        ("snippet", re.compile(r"\b4\d\d\b|try again later|temporarily unavailable|service temporarily", re.I)),
    ]),
    ("OUT_OF_OFFICE", [
        ("subject", re.compile(r"out of office|away|automatic reply|auto[\s-]?reply|vacation|on leave"
                               r"|abwesenheitsnotiz|abwesend", re.I)),
        ("snippet", re.compile(r"out of office|i am away|on annual leave|back on \d|ooo\b|automatic reply"
                               r"|abwesenheitsnotiz|abwesend|urlaub.{0,20}r[uü]ckkehr|nicht im b[uü]ro", re.I)),
    ]),
    ("AUTO_ACKNOWLEDGE", [
        ("subject", re.compile(
            r"\[[\w]+-[\w]+\]"                 # [TICKET-123456]
            r"|\[\d{1,2}-\d{10,}\]"            # [5-9110000040081]
            r"|request received|we received your"
            r"|your (request|inquiry|case) has been",
            re.I)),
        ("snippet", re.compile(
            r"received your (request|email|message)|we will process|has been logged"
            r"|case number|ticket number|reference number"
            r"|\[\d{1,2}-\d{10,}\]"            # Google ticket format
            r"|your (request|inquiry) has been received"
            r"|thank you for (contacting|reaching out).{0,80}(request|privacy|gdpr|data)"
            r"|representative will be in touch|will be in touch as soon"
            r"|a (member|representative|specialist|agent).{0,60}(will contact|will respond|will be in touch|will reach out)"
            r"|someone (from our team|will be in touch|will contact)"
            r"|automatisch generiert[ea]\s+(e-?mail|nachricht)"
            r"|wir haben (ihre|deine) (anfrage|e-?mail|nachricht) erhalten"
            r"|vielen dank.{0,50}(anfrage|kontakt)",
            re.I)),
    ]),
    ("CONFIRMATION_REQUIRED", [
        ("subject", re.compile(r"confirm (your )?request|verify (your )?request", re.I)),
        ("snippet", re.compile(
            r"will not begin processing.{0,40}until you have confirmed"
            r"|confirm.{0,20}request.{0,20}button"
            r"|confirm request"
            r"|hrtechprivacy\.com/confirm"
            r"|click.{0,30}confirm",
            re.I)),
    ]),
    ("IDENTITY_REQUIRED", [
        ("snippet", re.compile(
            r"proof of identity|verify your identity|copy of.{0,20}passport"
            r"|photo id|government[\s-]issued|id verification"
            r"|identity verification|verify.{0,20}identity",
            re.I)),
    ]),
    ("MORE_INFO_REQUIRED", [
        ("snippet", re.compile(
            r"please clarify|cannot identify|unable to locate.{0,20}record"
            r"|not clear which|additional information.{0,20}required"
            r"|need more information|require.{0,20}clarification",
            re.I)),
    ]),
    ("WRONG_CHANNEL", [
        ("snippet", re.compile(
            # Unmonitored inbox signals
            r"no longer monitored|won.t receive a response"
            r"|nicht gelesen|not read.{0,20}this mailbox"
            r"|use.{0,20}support form|this address is not monitored"
            r"|this mailbox is not monitored"
            # Portal/form redirect signals (merged from former REDIRECT_TO_PORTAL)
            r"|please submit via|privacy portal|dsar portal"
            r"|online form at|submit your request at"
            r"|requests\.hrtechprivacy\.com"
            r"|use our (online|web) (form|portal|tool)"
            # Self-service deflection — company telling user to manage data themselves
            r"|via our self-service|self-service portal|self-service tool"
            r"|can do so directly via"
            r"|via your (account|profile|dashboard)"
            # Channel mismatch — company says this request can't be handled here
            r"|not able to process.{0,60}(request|this).{0,60}(over chat|chat|this (channel|address|inbox))"
            r"|unable to process.{0,60}(request|this).{0,60}(over chat|chat|this (channel|address|inbox))"
            r"|this type of request.{0,60}(over chat|chat|email|this channel)"
            r"|pursue this (further|request).{0,40}(through|via)",
            re.I)),
    ]),
    ("REQUEST_ACCEPTED", [
        ("subject", re.compile(r"start of your request|confirmed|processing your request", re.I)),
        ("snippet", re.compile(
            r"confirmed your request and will begin"
            r"|begin gathering your data"
            r"|happy to make the privacy request on your behalf"
            r"|processing your subject access request"
            r"|will begin processing your (sar|subject access)",
            re.I)),
    ]),
    ("EXTENDED", [
        ("snippet", re.compile(
            r"require more time|three months|90 days"
            r"|extended the period|complex request.{0,30}additional time"
            r"|additional period of.{0,20}two months",
            re.I)),
    ]),
    ("IN_PROGRESS", [
        ("snippet", re.compile(
            r"currently processing|working on your request|in progress"
            r"|your request is being processed",
            re.I)),
    ]),
    ("DATA_PROVIDED_LINK", [
        ("subject", re.compile(
            r"download your.{0,30}personal data|data.{0,20}available|personal data.{0,20}complete"
            r"|your.{0,20}(data )?export.{0,20}ready|export.{0,20}(is )?ready"
            r"|data (request|export).{0,20}complete|your data.{0,20}ready"
            r"|data export|personal data export",
            re.I)),
        ("snippet", re.compile(
            r"data file is now available for download"
            r"|download your.{0,30}personal data"
            r"|download link will expire"
            r"|glassdoor\.com/dyd/download\?token="
            r"|access your.{0,20}data.{0,20}link"
            r"|your data is ready"
            r"|your.{0,20}export.{0,20}(is )?ready"
            r"|export.{0,20}available.{0,20}(for )?download"
            r"|download.{0,30}your.{0,30}export"
            r"|data export.{0,20}(is )?ready"
            r"|export.{0,20}complete.{0,20}download"
            r"|i.{0,5}(ve|have) attached.{0,50}(export|data|file|information)"
            r"|attached.{0,50}(export|personal data|information you requested)",
            re.I)),
    ]),
    ("DATA_PROVIDED_PORTAL", [
        ("snippet", re.compile(
            r"self-service account management page"
            r"|view.{0,20}download.{0,20}delete.{0,20}personal data"
            r"|access your data.{0,20}account"
            r"|manage your data.{0,20}settings"
            r"|account page.{0,30}download",
            re.I)),
    ]),
    ("REQUEST_DENIED", [
        ("snippet", re.compile(
            r"unable to comply|cannot fulfil|decline your request"
            r"|excessive|manifestly unfounded|cannot process your request",
            re.I)),
    ]),
    ("NO_DATA_HELD", [
        ("snippet", re.compile(
            r"no records.{0,20}about you|cannot locate.{0,20}your.{0,20}data"
            r"|not in our systems|no data held|unable to identify you"
            r"|no account.{0,20}associated"
            r"|do not hold.{0,30}data|not hold.{0,30}records"
            r"|hold no.{0,20}(data|information|records).{0,20}about you",
            re.I)),
    ]),
    ("NOT_GDPR_APPLICABLE", [
        ("snippet", re.compile(
            r"gdpr does not apply|not subject to gdpr"
            r"|outside the scope of gdpr|not.{0,20}eu.{0,10}uk.{0,10}resident"
            r"|not applicable under gdpr",
            re.I)),
    ]),
    # Google security/account notifications that land in SAR threads but are unrelated to the SAR
    ("NON_GDPR", [
        ("snippet", re.compile(
            r"account.{0,20}(recovered|recovery|has been recovered)"
            r"|google account.{0,30}(was|has been).{0,20}(accessed|recovered|changed|used)"
            r"|recent (security |account )?activity.{0,40}account"
            r"|someone (just )?signed in|new sign[\s-]?in",
            re.I)),
    ]),
    ("FULFILLED_DELETION", [
        ("snippet", re.compile(
            r"data has been deleted|account.{0,20}removed"
            r"|erasure.{0,20}complete|right to erasure.{0,20}fulfilled"
            r"|your data has been erased",
            re.I)),
    ]),
]

# ---------------------------------------------------------------------------
# Extraction regexes (applied after tagging)
# ---------------------------------------------------------------------------

_RE_REF_ZENDESK  = re.compile(r"\[[\w]+-[\d]+\]")
_RE_REF_GOOGLE   = re.compile(r"\[\d{1,2}-\d{10,}\]")
_RE_REF_TICKET   = re.compile(r"TICKET-\d{6}-\d+", re.I)
_RE_REF_GENERIC  = re.compile(r"Ref(?:erence)?[:#\s]\s*([\w-]+)", re.I)
_RE_CONFIRM_URL  = re.compile(r"https://requests\.hrtechprivacy\.com/confirm/[\w/-]+", re.I)
_RE_DOWNLOAD_URL = re.compile(
    r"https://\S+/dyd/download\?token=[^\s\u201c\u201d\"'<>]+"             # Glassdoor
    r"|https?://\S+(?:download|export)/[^\s\u201c\u201d\"'<>]{8,}"         # path-based export
    r"|https?://\S+(?:[?&])(?:token|key|export_id|download_id)=[^\s\u201c\u201d\"'<>]+",  # token param
    re.I,
    # Note: /attachments/token/ URLs (Zendesk/Substack) are handled by _RE_ZENDESK_ATTACHMENT_A
    # which is tried first to avoid concatenation of back-to-back entries.
)
# Context-aware extractor: any URL within 400 chars of data/export/download/attachment keywords.
# Used as fallback when _RE_DOWNLOAD_URL finds nothing (e.g. notification shells).
# Excludes ASCII and Unicode curly quotes from URL characters.
_RE_EXPORT_CONTEXT_URL = re.compile(
    r"(?:download|export|your data|data export|data file|personal data|dsar|sar|gdpr|attachment)"
    r".{0,400}(https?://[^\s\u201c\u201d\"'<>]+)"
    r"|"
    r"(https?://[^\s\u201c\u201d\"'<>]+).{0,400}"
    r"(?:download|export|your data|data export|data file|personal data|attachment)",
    re.I | re.S,
)
# Zendesk/Substack attachment URLs — two formats:
#   Format A: "filename.zip\nURL"  (expanded block — clean, no concatenation)
#   Format B: "filename.zip - URL" (compact inline — entries may be concatenated if no whitespace)
# Format A is tried first via finditer so clean lines are found before the concatenated compact line.
_RE_ZENDESK_ATTACHMENT_A = re.compile(
    r"[\w.-]{8,}\.(?:zip|json|csv|tar\.gz|gz)\r?\n(https?://[^\s\r\n\u201c\u201d\"'<>]+)",
    re.I,
)
_RE_ZENDESK_ATTACHMENT_B = re.compile(
    r"attachment[s]?\s*[:(].*?(https?://[^\s\u201c\u201d\"'<>]+\.(?:zip|json|csv|tar\.gz|gz))(?=[^a-zA-Z0-9]|$)",
    re.I | re.S,
)
# Characters to strip from the right end of any extracted URL
_URL_TRAILING_JUNK = re.compile(r'[\s.,;)\u201c\u201d\u2018\u2019"\']+$')

# Body-level AUTO_ACKNOWLEDGE detection: catches escalation/ticket signals buried below the snippet.
# Covers AI-bot auto-responders that assign a ticket and escalate to a human team.
_RE_BODY_AUTO_ACKNOWLEDGE = re.compile(
    r"escalating your request.{0,60}(human|specialist|team|privacy|data protection)"
    r"|ticket number[\s:]+TICKET-\d"
    r"|TICKET_REF:TICKET-"
    r"|you don.t need to do anything else at this point"
    r"|a member of.{0,60}(privacy|support|human).{0,40}(will review|will respond|will follow)",
    re.I | re.S,
)

# Body-level WRONG_CHANNEL detection: catches self-service deflection buried in the body.
# Matches responses where the company redirects to general account/settings pages rather
# than actually delivering data (e.g. Google "available to you through our online tools").
_RE_BODY_WRONG_CHANNEL = re.compile(
    r"already available.{0,80}(tools|services|account|portal)"
    r"|available to you through.{0,80}(tools|services)"
    r"|sign in to your.{0,40}account.{0,200}(access|manage|view).{0,50}(data|information)"
    r"|information.{0,30}(may be|is) already available",
    re.I | re.S,
)

_RE_PORTAL_URL   = re.compile(r"https?://\S+", re.I)  # fallback URL near portal keywords

# Tags considered "not yet useful" — LLM is called only when regex produced nothing at all.
# AUTO_ACKNOWLEDGE alone is intentionally excluded: regex/body-level detection is reliable
# enough, and calling LLM on top tends to pile on noisy secondary tags (IN_PROGRESS, HUMAN_REVIEW).
_LLM_TRIGGER_STATES = {frozenset()}

# Cache LLM results to avoid re-classifying identical auto-replies (domain reuse)
# Key: (from_addr, subject) — value: LLM result dict or None
_llm_cache: dict[tuple[str, str], dict | None] = {}

# ---------------------------------------------------------------------------
# NON_GDPR pre-pass patterns (Pass 0)
# Score-based: requires >= 2 independent signals to avoid false positives.
# A single noreply@ can legitimately send GDPR data downloads.
# ---------------------------------------------------------------------------
# Strong marketing signals (+2 each) — unambiguously non-GDPR senders
_NON_GDPR_FROM_LOCAL = re.compile(
    r"^(news|digest|jobs|marketing|career|noreply-jobs|community|newsletters?)$",
    re.I,
)
# Weaker signal (+1) — "alerts@" can legitimately send GDPR data-breach notices
_NON_GDPR_FROM_LOCAL_WEAK = re.compile(r"^alerts$", re.I)
_NON_GDPR_DISPLAY_NAME = re.compile(
    r"\b(jobs|alerts|digest|community|newsletter|marketing|career)\b",
    re.I,
)
_NON_GDPR_SUBJECT = re.compile(
    r"\b(job alert|newsletter|digest|weekly|community|new jobs|your daily|"
    r"recommendations|top picks|is hiring|apply now|open role)\b",
    re.I,
)
_NON_GDPR_SNIPPET = re.compile(
    r"unsubscribe|view this email in your browser|email preferences|opt out|manage your email",
    re.I,
)
# Zero-width characters are a reliable newsletter fingerprint —
# no legitimate GDPR response would contain invisible email-client spacers.
_NON_GDPR_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")


def _is_non_gdpr(from_addr: str, subject: str, snippet: str) -> bool:
    """Return True when multiple non-GDPR signals fire together.

    Scoring:
      - Email local part matches marketing prefix (alerts@, jobs@, etc.): +2
      - Display name contains marketing keyword (Glassdoor Jobs, Community…): +1
      - Subject matches newsletter/job patterns: +1
      - Snippet contains unsubscribe/view-in-browser language: +1
      - Snippet contains zero-width characters (newsletter spacers): +1
    Threshold: >= 2 required to avoid false positives on noreply@ GDPR emails.
    """
    from email.utils import parseaddr
    _display, email_addr = parseaddr(from_addr)
    local = email_addr.split("@")[0] if "@" in email_addr else ""

    signals = 0
    if local and _NON_GDPR_FROM_LOCAL.match(local):
        signals += 2
    elif local and _NON_GDPR_FROM_LOCAL_WEAK.match(local):
        signals += 1
    if _display and _NON_GDPR_DISPLAY_NAME.search(_display):
        signals += 1
    if _NON_GDPR_SUBJECT.search(subject):
        signals += 1
    if _NON_GDPR_SNIPPET.search(snippet):
        signals += 1
    if _NON_GDPR_ZERO_WIDTH.search(snippet):
        signals += 1
    return signals >= 2


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_RE_DATA_URL = re.compile(
    r"\.(zip|json|csv|tar\.gz|gz)(\?|$)"           # file extension in URL
    r"|/(download|export|dsar|sar|data[-_]request|attachments/token)/"  # path indicators
    r"|[?&](token|export_id|download_id|file)=",   # query param indicators
    re.I,
)


_RE_DATA_URL_EXCLUDE = re.compile(
    r"/(sub-processors?|vendors?|privacy[-_]policy|privacy|legal|cookies?|terms)"
    r"(?:/|$)",
    re.I,
)


def _is_data_url(url: str) -> bool:
    """Return True if a URL plausibly points to a data file rather than a generic webpage."""
    if _RE_DATA_URL_EXCLUDE.search(url):
        return False
    return bool(_RE_DATA_URL.search(url))


def classify(
    message: dict,
    *,
    api_key: str | None = None,
) -> ClassificationResult:
    """Classify a Gmail message dict into GDPR reply tags.

    Args:
        message: dict with keys: from, subject, snippet, has_attachment (bool)
        api_key: Anthropic API key for LLM fallback (optional)

    Returns:
        ClassificationResult with tags, extracted info, and llm_used flag
    """
    from_addr = message.get("from", "")
    subject   = message.get("subject", "")
    snippet   = message.get("snippet", "")
    body      = message.get("body", "")
    has_attachment = message.get("has_attachment", False)

    # --- Pass 0: NON_GDPR pre-pass ---
    if _is_non_gdpr(from_addr, subject, snippet):
        return ClassificationResult(
            tags=["NON_GDPR"],
            extracted=_extract(from_addr, subject, snippet),
            llm_used=False,
        )

    tags: list[str] = []

    # --- Pass 1: regex ---
    for tag, rules in _RULES:
        for field, pattern in rules:
            text = {"from": from_addr, "subject": subject, "snippet": snippet}.get(field, "")
            if pattern.search(text):
                tags.append(tag)
                break  # one match per tag is enough

    # If both bounce types detected, keep only the more specific one
    # (temporary 4xx signals override permanent classification)
    if "BOUNCE_TEMPORARY" in tags and "BOUNCE_PERMANENT" in tags:
        tags.remove("BOUNCE_PERMANENT")

    # DATA_PROVIDED_ATTACHMENT: attachment present, no clear link tag, not a bounce
    # Bounce notifications (e.g. mailer-daemon DSNs) also carry attachments (icons etc.)
    # — never treat those as data delivery.
    _is_bounce = "BOUNCE_PERMANENT" in tags or "BOUNCE_TEMPORARY" in tags
    if has_attachment and "DATA_PROVIDED_LINK" not in tags and not _is_bounce:
        tags.append("DATA_PROVIDED_ATTACHMENT")

    # --- Extraction ---
    extracted = _extract(from_addr, subject, snippet, body)

    # --- Body-level tag promotion ---
    # Pass 1 only checks subject/snippet. Some companies bury key language in the body.
    if body and not _is_bounce:
        # Escalation/ticket signals (AI bots that assign a ticket and hand off to humans)
        if "AUTO_ACKNOWLEDGE" not in tags and _RE_BODY_AUTO_ACKNOWLEDGE.search(body):
            tags.append("AUTO_ACKNOWLEDGE")
        # Self-service deflection (e.g. Google "available through our online tools")
        if "WRONG_CHANNEL" not in tags and _RE_BODY_WRONG_CHANNEL.search(body):
            tags.append("WRONG_CHANNEL")

    # --- Link-first promotion ---
    # If URL extraction found a data link but the regex pass didn't fire DATA_PROVIDED_LINK,
    # promote the tag here. Covers notification-shell emails (e.g. Substack, similar services)
    # where the body contains a download URL but the subject/snippet use non-standard phrasing.
    # Guard: only promote if the URL looks like a real data file/download, not a generic
    # webpage (e.g. privacy policy link that was context-matched near the word "privacy").
    if (
        extracted.get("data_link")
        and _is_data_url(extracted["data_link"])
        and "DATA_PROVIDED_LINK" not in tags
        and not _is_bounce
    ):
        tags.append("DATA_PROVIDED_LINK")

    # --- Pass 2: LLM fallback ---
    llm_used = False
    if frozenset(tags) in _LLM_TRIGGER_STATES and api_key:
        cache_key = (from_addr, subject)
        if cache_key in _llm_cache:
            llm_result = _llm_cache[cache_key]
        else:
            llm_result = _llm_classify(message, api_key)
            _llm_cache[cache_key] = llm_result
        if llm_result:
            tags = llm_result.get("tags", tags)
            extracted.update({k: v for k, v in llm_result.items() if k in extracted and v})
            llm_used = True
            # HUMAN_REVIEW is a last-resort fallback — drop it if any substantive tag is present
            if "HUMAN_REVIEW" in tags and len(tags) > 1:
                tags = [t for t in tags if t != "HUMAN_REVIEW"]

    if not tags:
        tags = ["HUMAN_REVIEW"]

    return ClassificationResult(tags=tags, extracted=extracted, llm_used=llm_used)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract(from_addr: str, subject: str, snippet: str, body: str = "") -> dict:
    """Extract structured fields from raw message text.

    Searches subject+snippet for reference numbers (always in headers).
    Searches full body text for URLs — download links are often buried below
    the visible Gmail snippet.
    """
    text = f"{subject} {snippet}"
    full_text = f"{text} {body}" if body else text

    # Reference numbers: search subject+snippet first, fall back to body
    reference_number = ""
    for pattern in (_RE_REF_ZENDESK, _RE_REF_GOOGLE, _RE_REF_TICKET):
        m = pattern.search(text) or pattern.search(full_text)
        if m:
            reference_number = m.group(0)
            break
    if not reference_number:
        m = _RE_REF_GENERIC.search(text) or _RE_REF_GENERIC.search(full_text)
        if m:
            reference_number = m.group(1)

    # URLs: search full body so time-limited download links are captured
    confirmation_url = ""
    m = _RE_CONFIRM_URL.search(full_text)
    if m:
        confirmation_url = m.group(0)

    def _clean_url(u: str) -> str:
        return _URL_TRAILING_JUNK.sub("", u)

    # Collect all data download URLs (some companies, e.g. Substack, send multiple zip files).
    # Strategy: try cleanest extractors first; only fall through if nothing found.
    data_links: list[str] = []

    # Pass A: Zendesk expanded format — "filename.zip\nURL" (clean, no concatenation risk).
    # Run this BEFORE the generic _RE_DOWNLOAD_URL which has a greedy /attachments/token/ arm
    # that concatenates back-to-back Zendesk entries.
    for m in _RE_ZENDESK_ATTACHMENT_A.finditer(full_text):
        url = _clean_url(m.group(1))
        if url and url not in data_links:
            data_links.append(url)

    if not data_links:
        # Pass B: generic download URL patterns (Glassdoor, path-based, token params).
        # Excludes /attachments/token/ — handled above via Pass A.
        for m in _RE_DOWNLOAD_URL.finditer(full_text):
            url = _clean_url(m.group(0))
            if url and url not in data_links:
                data_links.append(url)

    if not data_links:
        # Pass C: Zendesk compact inline — "Attachment(s): filename.zip - URL …"
        # Used when the expanded block isn't present.
        for m in _RE_ZENDESK_ATTACHMENT_B.finditer(full_text):
            url = _clean_url(m.group(1))
            if url and url not in data_links:
                data_links.append(url)

    if not data_links:
        # Pass D: any URL near data/export/download/attachment keywords in the body.
        m = _RE_EXPORT_CONTEXT_URL.search(full_text)
        if m:
            url = _clean_url(m.group(1) or m.group(2) or "")
            if url:
                data_links.append(url)

    data_link = data_links[0] if data_links else ""

    # Portal URL: first URL near portal keywords
    portal_url = ""
    portal_context = re.search(
        r"(https?://\S+).{0,200}(portal|submit|form)|"
        r"(portal|submit|form).{0,200}(https?://\S+)",
        full_text, re.I | re.S,
    )
    if portal_context:
        url_match = re.search(r"https?://[^\s\u201c\u201d\"'<>]+", portal_context.group(0))
        if url_match:
            portal_url = _clean_url(url_match.group(0))

    return {
        "reference_number": reference_number,
        "confirmation_url": confirmation_url,
        "data_link": data_link,          # first URL (backward compat)
        "data_links": data_links,        # all URLs (e.g. Substack sends 2 zips)
        "portal_url": portal_url,
        "deadline_extension_days": None,
        "summary": "",                   # filled by LLM fallback path only
    }


def reextract_data_links(reply_record_dict: dict, body: str) -> dict:
    """Re-run URL extraction on a stored ReplyRecord dict using a fresh email body.

    Used to fix existing records where data_link is empty because the URL was
    buried below the Gmail snippet at classification time.

    Args:
        reply_record_dict: Raw dict from ReplyRecord.to_dict()
        body:              Full email body text fetched fresh from Gmail

    Returns:
        Updated extracted dict. Caller must persist to state.
    """
    from_addr = reply_record_dict.get("from_addr", "")
    subject   = reply_record_dict.get("subject", "")
    snippet   = reply_record_dict.get("snippet", "")
    existing  = dict(reply_record_dict.get("extracted", {}))

    new_extracted = _extract(from_addr, subject, snippet, body)
    # Only fill in missing fields — never overwrite non-empty values
    for key in ("data_link", "confirmation_url", "portal_url"):
        if not existing.get(key) and new_extracted.get(key):
            existing[key] = new_extracted[key]
    return existing


_ACTION_DRAFT_TAGS: frozenset[str] = frozenset({
    "WRONG_CHANNEL", "MORE_INFO_REQUIRED", "CONFIRMATION_REQUIRED",
    "IDENTITY_REQUIRED", "HUMAN_REVIEW",
})

_ACTION_DRAFT_TAG_LABELS: dict[str, str] = {
    "WRONG_CHANNEL":         "company redirected to a different channel without specifying which",
    "MORE_INFO_REQUIRED":    "company asked for more information",
    "CONFIRMATION_REQUIRED": "company requires you to confirm the request",
    "IDENTITY_REQUIRED":     "company requires identity verification",
    "HUMAN_REVIEW":          "reply needs manual review — unclear response",
}


def generate_reply_draft(
    reply_body: str,
    tags: list[str],
    company_name: str,
    *,
    api_key: str | None = None,
) -> str:
    """Call Claude Haiku to draft a follow-up reply. Returns empty string on failure."""
    if not api_key:
        return ""
    action_tags = [t for t in tags if t in _ACTION_DRAFT_TAGS]
    if not action_tags:
        return ""
    try:
        import anthropic
        from contact_resolver import cost_tracker

        issues = "; ".join(_ACTION_DRAFT_TAG_LABELS.get(t, t) for t in action_tags)
        prompt = (
            "You are helping a data subject follow up on a GDPR Subject Access Request.\n"
            "The company has replied but the response is unclear or requires action.\n\n"
            f"Company: {company_name}\n"
            f"Detected issue(s): {issues}\n"
            f"Their reply:\n{reply_body[:3000]}\n\n"
            "Write a concise professional follow-up email body (3-5 sentences, no salutation, no sign-off).\n"
            "The reply should acknowledge their message and ask for the specific clarification needed.\n"
            "Reference GDPR Article 15 rights if relevant. Return ONLY the email body text."
        )
        model = "claude-haiku-4-5-20251001"
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        cost_tracker.record_llm_call(
            company_name=company_name,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=model,
            found=True,
            source="reply_draft",
            purpose="Follow-up reply drafting",
        )
        return response.content[0].text.strip()
    except Exception as exc:
        print(f"[classifier] generate_reply_draft failed: {exc}")
        return ""


def _llm_classify(message: dict, api_key: str) -> dict | None:
    """Call Claude Haiku to classify a message that regex couldn't tag."""
    try:
        import anthropic
        from contact_resolver import cost_tracker

        client = anthropic.Anthropic(api_key=api_key)
        body_preview = (message.get("body", "") or "")[:1500]
        subject = message.get("subject", "")
        # Detect request type from subject so the LLM has accurate context
        if "subprocessor" in subject.lower():
            request_type = "Subprocessor Disclosure Request (asking company to list their data sub-processors under GDPR Art. 13/14)"
        else:
            request_type = "Subject Access Request (SAR) under GDPR Article 15"
        prompt = (
            "You are a GDPR compliance assistant. Classify this email reply to a GDPR request.\n\n"
            f"Request type sent: {request_type}\n"
            f"From: {message.get('from', '')}\n"
            f"Subject: {subject}\n"
            f"Body snippet: {message.get('snippet', '')}\n"
            f"Body (first 1500 chars): {body_preview}\n\n"
            "## Tag definitions\n"
            "Choose one or more tags from this list. Definitions are strict — do not invent tags.\n\n"
            "AUTO_ACKNOWLEDGE — Company acknowledged receipt and will respond later. This is the correct tag for:\n"
            "  - Any generic 'we received your email / a representative will be in touch / someone will get back to you' reply\n"
            "  - Support desk auto-replies ('Thanks for contacting us, our team will respond shortly')\n"
            "  - Ticket/case/reference number assigned\n"
            "  - Request escalated to privacy/data protection team\n"
            "  - Bot replies that say 'you don't need to do anything else'\n"
            "  Key: if the email says nothing actionable and just promises a future response, it is AUTO_ACKNOWLEDGE.\n\n"
            "REQUEST_ACCEPTED — Company explicitly confirmed they are processing the GDPR request (beyond mere receipt).\n\n"
            "IN_PROGRESS — Company confirmed work has started (e.g. 'we are compiling your data').\n\n"
            "EXTENDED — Company invoked the 2-month extension under GDPR Art. 12(3). Extract days if stated.\n\n"
            "IDENTITY_REQUIRED — Company requires proof of identity before proceeding.\n\n"
            "CONFIRMATION_REQUIRED — Company requires the user to click a link or reply to confirm the request.\n\n"
            "MORE_INFO_REQUIRED — Company cannot process without additional information from the user.\n\n"
            "WRONG_CHANNEL — Company says this email address is unmonitored, or directs to a portal/form instead.\n\n"
            "DATA_PROVIDED_LINK — Email contains a URL to download the user's data export.\n\n"
            "DATA_PROVIDED_ATTACHMENT — Email has an attached file containing the user's data.\n\n"
            "DATA_PROVIDED_PORTAL — Company says data is available via a self-service portal (no direct link).\n\n"
            "REQUEST_DENIED — Company refuses to fulfil the request, citing a legal basis.\n\n"
            "NO_DATA_HELD — Company confirms they hold no personal data about the user.\n\n"
            "NOT_GDPR_APPLICABLE — Company explicitly states the user is not covered by GDPR.\n\n"
            "FULFILLED_DELETION — Company confirmed deletion/erasure is complete.\n\n"
            "OUT_OF_OFFICE — Sender is on leave; auto-reply with return date.\n\n"
            "BOUNCE_PERMANENT — Delivery failure; address does not exist.\n\n"
            "BOUNCE_TEMPORARY — Delivery failure; mailbox full or temporary error.\n\n"
            "NON_GDPR — Email is unrelated to the GDPR request (security alert, marketing, billing, etc.).\n\n"
            "HUMAN_REVIEW — LAST RESORT ONLY. Use only when the email is clearly a substantive GDPR response "
            "(contains legal analysis, specific decisions, or detailed data) but genuinely fits none of the tags above. "
            "NEVER use alongside AUTO_ACKNOWLEDGE. NEVER use for generic 'we'll be in touch' replies.\n\n"
            "## Output format\n"
            "Reply with JSON only, no explanation:\n"
            "{\n"
            '  "tags": [<one or more tag strings from the list above>],\n'
            '  "reference_number": <ticket/case ref string, or null>,\n'
            '  "confirmation_url": <URL to confirm request, or null>,\n'
            '  "data_link": <first data export URL, or null>,\n'
            '  "portal_url": <self-service portal URL, or null>,\n'
            '  "deadline_extension_days": <integer days of extension, or null>,\n'
            '  "summary": <one plain-English sentence ≤15 words describing what this reply says>\n'
            "}"
        )
        model = "claude-haiku-4-5-20251001"
        response = client.messages.create(
            model=model,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        cost_tracker.record_llm_call(
            company_name=message.get("from", "?"),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=model,
            found=True,
            source="reply_classifier",
            purpose="Reply email classification",
        )
        raw = response.content[0].text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
        return json.loads(raw)
    except Exception as exc:
        print(f"[classifier] _llm_classify failed: {exc}")
        return None
