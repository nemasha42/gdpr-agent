"""Data models for GDPR reply monitoring."""

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Tag constants
# ---------------------------------------------------------------------------

REPLY_TAGS = [
    "AUTO_ACKNOWLEDGE",
    "OUT_OF_OFFICE",
    "BOUNCE_PERMANENT",
    "BOUNCE_TEMPORARY",
    "CONFIRMATION_REQUIRED",
    "IDENTITY_REQUIRED",
    "MORE_INFO_REQUIRED",
    "WRONG_CHANNEL",
    "REQUEST_ACCEPTED",
    "EXTENDED",
    "IN_PROGRESS",
    "DATA_PROVIDED_LINK",
    "DATA_PROVIDED_ATTACHMENT",
    "DATA_PROVIDED_PORTAL",
    "REQUEST_DENIED",
    "NO_DATA_HELD",
    "NOT_GDPR_APPLICABLE",
    "FULFILLED_DELETION",
    "HUMAN_REVIEW",
]

# Derived company statuses (computed, never stored)
COMPANY_STATUSES = [
    "PENDING",
    "BOUNCED",
    "ACKNOWLEDGED",
    "ACTION_REQUIRED",
    "EXTENDED",
    "COMPLETED",
    "DENIED",
    "OVERDUE",
]


# ---------------------------------------------------------------------------
# Attachment models
# ---------------------------------------------------------------------------


@dataclass
class FileEntry:
    filename: str
    size_bytes: int
    file_type: str  # file extension without dot


@dataclass
class AttachmentCatalog:
    path: str           # local path to downloaded file
    size_bytes: int
    file_type: str      # "zip", "json", "csv", "pdf", etc.
    files: list[FileEntry] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    schema: list[dict] = field(default_factory=list)  # LLM-inferred category schemas
    services: list[dict] = field(default_factory=list)  # [{name, description}]
    export_meta: dict = field(default_factory=dict)  # {format, delivery, timeline}

    # Convenience alias used in templates
    @property
    def total_size_bytes(self) -> int:
        return self.size_bytes

    @property
    def received_at(self) -> str:
        """Not stored — callers pass this separately via template context."""
        return ""

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "file_type": self.file_type,
            "files": [
                {"filename": f.filename, "size_bytes": f.size_bytes, "file_type": f.file_type}
                for f in self.files
            ],
            "categories": self.categories,
            "schema": self.schema,
            "services": self.services,
            "export_meta": self.export_meta,
        }


# ---------------------------------------------------------------------------
# Classification result
# ---------------------------------------------------------------------------


@dataclass
class ClassificationResult:
    tags: list[str]
    extracted: dict  # reference_number, confirmation_url, data_link, portal_url, deadline_extension_days
    llm_used: bool = False


# ---------------------------------------------------------------------------
# Reply record
# ---------------------------------------------------------------------------


@dataclass
class ReplyRecord:
    gmail_message_id: str
    received_at: str
    from_addr: str
    subject: str
    snippet: str
    tags: list[str]
    extracted: dict
    llm_used: bool
    has_attachment: bool
    attachment_catalog: dict | None

    def to_dict(self) -> dict:
        return {
            "gmail_message_id": self.gmail_message_id,
            "received_at": self.received_at,
            "from": self.from_addr,
            "subject": self.subject,
            "snippet": self.snippet,
            "tags": self.tags,
            "extracted": self.extracted,
            "llm_used": self.llm_used,
            "has_attachment": self.has_attachment,
            "attachment_catalog": self.attachment_catalog,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ReplyRecord":
        return cls(
            gmail_message_id=d["gmail_message_id"],
            received_at=d["received_at"],
            from_addr=d["from"],
            subject=d["subject"],
            snippet=d["snippet"],
            tags=d["tags"],
            extracted=d["extracted"],
            llm_used=d["llm_used"],
            has_attachment=d["has_attachment"],
            attachment_catalog=d.get("attachment_catalog"),
        )


# ---------------------------------------------------------------------------
# Company state
# ---------------------------------------------------------------------------


@dataclass
class CompanyState:
    domain: str
    company_name: str
    sar_sent_at: str          # ISO datetime string
    to_email: str
    subject: str
    gmail_thread_id: str
    deadline: str             # ISO date YYYY-MM-DD
    replies: list[ReplyRecord] = field(default_factory=list)
    last_checked: str = ""

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "company_name": self.company_name,
            "sar_sent_at": self.sar_sent_at,
            "to_email": self.to_email,
            "subject": self.subject,
            "gmail_thread_id": self.gmail_thread_id,
            "deadline": self.deadline,
            "replies": [r.to_dict() for r in self.replies],
            "last_checked": self.last_checked,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CompanyState":
        replies = [ReplyRecord.from_dict(r) for r in d.get("replies", [])]
        return cls(
            domain=d["domain"],
            company_name=d["company_name"],
            sar_sent_at=d["sar_sent_at"],
            to_email=d["to_email"],
            subject=d["subject"],
            gmail_thread_id=d.get("gmail_thread_id", ""),
            deadline=d["deadline"],
            replies=replies,
            last_checked=d.get("last_checked", ""),
        )
