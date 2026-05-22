import json
import pathlib
import sys

cache_file = pathlib.Path(".ans_cache.json")
downloads = pathlib.Path("./downloads")

if not cache_file.exists():
    print("No cache file found. Run 'just download' first.")
    sys.exit(1)

cache = json.loads(cache_file.read_text())

missing = []
for folder_id, info in cache.items():
    if not info.get("course_code") or not info.get("course_name"):
        folder_path = downloads / folder_id
        if folder_path.exists():
            missing.append(folder_id)

if not missing:
    print("All downloads have course codes and names assigned!")
    sys.exit(0)

print(f"Found {len(missing)} download(s) without complete course information.\n")

# Ask each
for folder_id in missing:
    print(f"\nFolder: {folder_id}")
    folder_path = downloads / folder_id

    # Get title
    import subprocess

    try:
        title = subprocess.check_output(["just", "_get_title", str(folder_path)], text=True).strip()
        if title:
            print(f"Title: {title}")
    except:
        pass

    # Show existing values if any
    existing_code = cache[folder_id].get("course_code")
    existing_name = cache[folder_id].get("course_name")
    if existing_code:
        print(f"Current code: {existing_code}")
    if existing_name:
        print(f"Current name: {existing_name}")

    while True:
        code = input("Enter course code: ").strip()
        if code:
            break
        print("Course code is required!")

    while True:
        name = input("Enter course name: ").strip()
        if name:
            break
        print("Course name is required!")

    cache[folder_id]["course_code"] = code
    cache[folder_id]["course_name"] = name
    print(f"✓ Added: {code} - {name}")

# Save updated cache
cache_file.write_text(json.dumps(cache, indent=2))
print("\n✓ Cache updated!")
