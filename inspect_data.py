
import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def read_jsonl(path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)

def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

print("=== DATA INSPECTION ===")

event_count = 0
event_ids = set()
duplicates = 0
event_types = Counter()
users = set()

for event in read_jsonl(ROOT / "data" / "events.jsonl"):
    event_count += 1

    event_id = event.get("event_id")
    if event_id:
        if event_id in event_ids:
            duplicates += 1
        event_ids.add(event_id)

    if event.get("user_id"):
        users.add(event["user_id"])

    event_types[event.get("event", "UNKNOWN")] += 1

print("Historical event lines:", event_count)
print("Unique users:", len(users))
print("Duplicate event IDs:", duplicates)
print("Event types:", event_types.most_common(10))

for name in [
    "profiles_snapshot.csv",
    "catalogue.csv",
    "episodes.csv",
]:
    path = ROOT / "data" / name

    if path.exists():
        rows = read_csv(path)
        print(name, "rows:", len(rows))
        if rows:
            print("  columns:", list(rows[0].keys()))
    else:
        print(name, "MISSING")

trigger_count = sum(
    1 for _ in read_jsonl(ROOT / "replay" / "triggers.jsonl")
)
print("Replay triggers:", trigger_count)

arms = read_csv(ROOT / "replay" / "arms.csv")
print("Replay arm rows:", len(arms))
print(
    "Arm distribution:",
    Counter(row["arm"] for row in arms)
)

print("Inspection finished.")
