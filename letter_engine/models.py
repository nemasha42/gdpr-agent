"""Data model for a composed SAR letter."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class SARLetter:
    company_name: str
    method: Literal["email", "portal", "postal"]
    to_email: str       # set when method == "email"
    subject: str        # set when method == "email"
    body: str           # filled template — always present
    portal_url: str     # set when method == "portal"
    postal_address: str # formatted recipient address when method == "postal"
    gmail_message_id: str = field(default="")   # populated after successful Gmail send
    gmail_thread_id: str = field(default="")    # populated after successful Gmail send
