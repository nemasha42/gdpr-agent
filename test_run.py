from auth.gmail_oauth import get_gmail_service
from scanner.inbox_reader import fetch_emails

service = get_gmail_service()
emails = fetch_emails(service, max_results=20)
for e in emails:
    print(e['sender'], '|', e['subject'])
