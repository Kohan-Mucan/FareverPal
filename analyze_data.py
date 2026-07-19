
from farever_companion.data.raw_data import DATA
from collections import Counter

for key, value in DATA.items():
    if isinstance(value, list):
        ids = [item.get('id') for item in value if isinstance(item, dict) and 'id' in item]
        counts = Counter(ids)
        duplicates = {k: v for k, v in counts.items() if v > 1}
        if duplicates:
            print(f"Key '{key}' has {len(duplicates)} duplicate IDs. Total items: {len(ids)}, Unique: {len(counts)}")
            print(f"  First few: {list(duplicates.items())[:5]}")
        else:
            print(f"Key '{key}' has no duplicate IDs. Total items: {len(ids)}")
    elif isinstance(value, dict):
        print(f"Key '{key}' is a dict with {len(value)} keys.")
    else:
        print(f"Key '{key}' is {type(value)}.")
