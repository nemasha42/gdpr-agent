import json

data = {
    "meta": {"version": "1.0", "last_updated": "", "total_companies": 0},
    "companies": {},
}
with open("data/companies.json", "w") as f:
    json.dump(data, f, indent=2)
print("Cache cleared")
