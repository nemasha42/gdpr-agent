import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

# Load .env from project root (two levels up from this file)
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)


class Settings(BaseModel):
    model_config = {"frozen": True}

    google_client_id: str
    google_client_secret: str
    anthropic_api_key: str
    user_full_name: str
    user_email: str
    user_address_line1: str
    user_address_city: str
    user_address_postcode: str
    user_address_country: str
    gdpr_framework: str


def get_settings() -> Settings:
    return Settings(
        google_client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
        google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        user_full_name=os.getenv("USER_FULL_NAME", ""),
        user_email=os.getenv("USER_EMAIL", ""),
        user_address_line1=os.getenv("USER_ADDRESS_LINE1", ""),
        user_address_city=os.getenv("USER_ADDRESS_CITY", ""),
        user_address_postcode=os.getenv("USER_ADDRESS_POSTCODE", ""),
        user_address_country=os.getenv("USER_ADDRESS_COUNTRY", ""),
        gdpr_framework=os.getenv("GDPR_FRAMEWORK", "UK GDPR"),
    )


settings = get_settings()
