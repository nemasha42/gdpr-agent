from auth.gmail_oauth import get_gmail_service
from scanner.inbox_reader import fetch_emails
from scanner.service_extractor import extract_services
from scanner.company_normalizer import normalize_domain

service = get_gmail_service()
emails = fetch_emails(service, max_results=100)
services = extract_services(emails)

for s in services:
    print(f"{s['company_name_raw']:30} | {s['confidence']:6} | {s['domain']}")
