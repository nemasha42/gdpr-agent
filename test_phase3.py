from contact_resolver.resolver import ContactResolver

resolver = ContactResolver()

test_companies = [
    ("Glassdoor", "glassdoor.com"),
    ("Google", "google.com"),
    ("PayPal", "paypal.com"),
    ("Substack", "substack.com"),
    ("Hardware FYI", "hardwarefyi.com"),
    ("ProxyDocs", "proxydocs.com"),
    ("Reflexivity", "reflexivity.com"),
    ("Polymarket", "polymarket.com"),
]

for company_name, domain in test_companies:
    print(f"\nLooking up: {company_name} ({domain})")
    result = resolver.resolve(domain, company_name)
    if result:
        print(f"  Source:  {result.source}")
        print(f"  Method:  {result.contact.preferred_method}")
        print(f"  Email:   {result.contact.privacy_email or result.contact.dpo_email}")
        print(f"  Portal:  {result.contact.gdpr_portal_url}")
    else:
        print("  Not found — needs manual entry")
from contact_resolver.cost_tracker import print_cost_summary
print_cost_summary()
