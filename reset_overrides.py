import json

with open("data/dataowners_overrides.json", "w") as f:
    json.dump({}, f, indent=2)
print("dataowners_overrides.json cleared")
