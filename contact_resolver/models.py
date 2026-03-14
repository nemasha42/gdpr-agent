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


class CompanyRecord(BaseModel):
    company_name: str
    legal_entity_name: str = ""
    source: Literal["datarequests", "llm_search", "user_manual"]
    source_confidence: Literal["high", "medium", "low"]
    last_verified: str  # ISO date: YYYY-MM-DD
    contact: Contact = Field(default_factory=Contact)
    flags: Flags = Field(default_factory=Flags)
    request_notes: RequestNotes = Field(default_factory=RequestNotes)


class DBMeta(BaseModel):
    version: str = "1.0"
    last_updated: str = ""
    total_companies: int = 0


class CompaniesDB(BaseModel):
    meta: DBMeta = Field(default_factory=DBMeta)
    companies: dict[str, CompanyRecord] = Field(default_factory=dict)
