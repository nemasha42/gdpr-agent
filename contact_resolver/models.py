"""Pydantic models for company contact records and the companies.json database."""

from typing import Literal

from pydantic import BaseModel, Field


class PostalAddress(BaseModel):
    line1: str = ""
    city: str = ""
    postcode: str = ""
    country: str = ""


class Contact(BaseModel):
    dpo_email: str = ""
    privacy_email: str = ""
    gdpr_portal_url: str = ""
    postal_address: PostalAddress = Field(default_factory=PostalAddress)
    preferred_method: Literal["email", "portal", "postal"] = "email"


class Flags(BaseModel):
    portal_only: bool = False
    email_accepted: bool = True
    auto_send_possible: bool = False


class RequestNotes(BaseModel):
    special_instructions: str = ""
    identity_verification_required: bool = False
    known_response_time_days: int = 30


class Subprocessor(BaseModel):
    domain: str
    company_name: str
    hq_country: str = ""
    hq_country_code: str = ""
    purposes: list[str] = Field(default_factory=list)
    data_categories: list[str] = Field(default_factory=list)
    transfer_basis: Literal[
        "adequacy_decision", "SCCs", "BCRs", "consent", "none", "unknown"
    ] = "unknown"
    source_url: str = ""
    source: Literal[
        "scrape_subprocessor_page", "scrape_privacy_policy", "llm_search"
    ] = "llm_search"
    last_fetched: str = ""
    sub_subprocessors: list["Subprocessor"] = Field(default_factory=list)


Subprocessor.model_rebuild()


class SubprocessorRecord(BaseModel):
    fetched_at: str
    source_url: str = ""
    subprocessors: list[Subprocessor] = Field(default_factory=list)
    fetch_status: Literal["ok", "not_found", "error", "pending"] = "pending"
    error_message: str = ""


class PortalFormField(BaseModel):
    name: str  # AXTree element name, e.g. "First Name"
    value_key: str  # key into user_data dict, e.g. "first_name"
    role: str  # AXTree role: "textbox", "combobox", "checkbox"


class PortalFieldMapping(BaseModel):
    cached_at: str = ""
    platform: str = ""  # "onetrust", "trustarc", "salesforce", "unknown"
    fields: list[PortalFormField] = Field(default_factory=list)
    submit_button: str = ""


class CompanyRecord(BaseModel):
    company_name: str
    legal_entity_name: str = ""
    source: Literal[
        "datarequests",
        "llm_search",
        "user_manual",
        "dataowners_override",
        "privacy_scrape",
    ]
    source_confidence: Literal["high", "medium", "low"]
    last_verified: str  # ISO date: YYYY-MM-DD
    contact: Contact = Field(default_factory=Contact)
    flags: Flags = Field(default_factory=Flags)
    request_notes: RequestNotes = Field(default_factory=RequestNotes)
    subprocessors: SubprocessorRecord | None = None
    portal_field_mapping: PortalFieldMapping | None = None


class DBMeta(BaseModel):
    version: str = "1.0"
    last_updated: str = ""
    total_companies: int = 0


class CompaniesDB(BaseModel):
    meta: DBMeta = Field(default_factory=DBMeta)
    companies: dict[str, CompanyRecord] = Field(default_factory=dict)
