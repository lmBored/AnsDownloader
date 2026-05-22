set shell := ["zsh", "-cu"]
CACHE_FILE := ".ans_cache.json"

# Default: show help
default:
    @just --list

# Download an exam given its URL
download:
    uv run python main.py
    @just _update_cache

# Extract title from HTML files
[private]
_get_title DIR:
    #!/usr/bin/env python3
    import sys, re, html, pathlib
    base = pathlib.Path("{{DIR}}")
    for name in ("overview.html", "question_1.html"):
        p = base / name
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"<title>\s*(.*?)\s*</title>", text, re.I | re.S)
        if m:
            t = html.unescape(m.group(1))
            t = re.sub(r"^\s*View\s*\u00b7\s*", "", t)
            print(t.strip())
            sys.exit(0)
        m = re.search(r"<h1[^>]*>\s*([^<]+)\s*</h1>", text, re.I | re.S)
        if m:
            print(html.unescape(m.group(1)).strip())
            sys.exit(0)
    print("")

# Update cache with existing downloads
[private]
_update_cache:
    #!/usr/bin/env python3
    import html
    import json
    import pathlib
    import re
    cache_file = pathlib.Path("{{CACHE_FILE}}")
    downloads = pathlib.Path("./downloads")

    def extract_title(folder: pathlib.Path) -> str:
        for name in ("overview.html", "question_1.html"):
            p = folder / name
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r"<title>\s*(.*?)\s*</title>", text, re.I | re.S)
            if m:
                t = html.unescape(m.group(1))
                t = re.sub(r"^\s*View\s*\u00b7\s*", "", t)
                return t.strip()
            m = re.search(r"<h1[^>]*>\s*([^<]+)\s*</h1>", text, re.I | re.S)
            if m:
                return html.unescape(m.group(1)).strip()
        return ""

    cache: dict[str, dict] = {}
    if cache_file.exists():
        try:
            loaded = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cache = loaded
        except Exception:
            cache = {}

    if downloads.exists():
        def sort_key(p: pathlib.Path):
            name = p.name
            return (0, int(name)) if name.isdigit() else (1, name)

        for folder in sorted((p for p in downloads.iterdir() if p.is_dir()), key=sort_key):
            folder_id = folder.name
            info = cache.get(folder_id)
            if not isinstance(info, dict):
                info = {}
            info.setdefault("course_code", None)
            info.setdefault("course_name", None)

            # Optional metadata to make `just list`/`just serve` faster
            folder_mtime = int(folder.stat().st_mtime)
            if info.get("_scan_mtime") != folder_mtime:
                info["html_count"] = len(list(folder.glob("*.html")))
                title = extract_title(folder)
                if title:
                    info["title"] = title
                info["_scan_mtime"] = folder_mtime
            else:
                if info.get("html_count") is None:
                    info["html_count"] = len(list(folder.glob("*.html")))
                if not info.get("title"):
                    title = extract_title(folder)
                    if title:
                        info["title"] = title

            cache[folder_id] = info

    new_text = json.dumps(cache, indent=2)
    if cache_file.exists():
        try:
            old_text = cache_file.read_text(encoding="utf-8")
        except Exception:
            old_text = None
        if old_text == new_text:
            raise SystemExit(0)

    cache_file.write_text(new_text, encoding="utf-8")

# Listing of downloads with cache info
[private]
_downloads_rows:
    #!/usr/bin/env python3
    import html
    import json
    import pathlib
    import re

    cache_file = pathlib.Path("{{CACHE_FILE}}")
    downloads = pathlib.Path("./downloads")

    def extract_title(folder: pathlib.Path) -> str:
        for name in ("overview.html", "question_1.html"):
            p = folder / name
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r"<title>\s*(.*?)\s*</title>", text, re.I | re.S)
            if m:
                t = html.unescape(m.group(1))
                t = re.sub(r"^\s*View\s*\u00b7\s*", "", t)
                return t.strip()
            m = re.search(r"<h1[^>]*>\s*([^<]+)\s*</h1>", text, re.I | re.S)
            if m:
                return html.unescape(m.group(1)).strip()
        return ""

    cache: dict[str, dict] = {}
    if cache_file.exists():
        try:
            loaded = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cache = loaded
        except Exception:
            cache = {}

    if not downloads.exists():
        raise SystemExit(0)

    def folder_sort_key(p: pathlib.Path):
        name = p.name
        return (0, int(name)) if name.isdigit() else (1, name)

    rows = []
    for folder in sorted((p for p in downloads.iterdir() if p.is_dir()), key=folder_sort_key):
        folder_id = folder.name
        info = cache.get(folder_id) if isinstance(cache.get(folder_id), dict) else {}
        folder_mtime = int(folder.stat().st_mtime)

        code = (info.get("course_code") or "").strip() if isinstance(info.get("course_code"), str) else info.get("course_code") or ""
        name = (info.get("course_name") or "").strip() if isinstance(info.get("course_name"), str) else info.get("course_name") or ""

        title = info.get("title") or ""
        if not isinstance(title, str):
            title = ""
        if not title or info.get("_scan_mtime") != folder_mtime:
            title = extract_title(folder)

        html_count = info.get("html_count")
        if not isinstance(html_count, int):
            html_count = len(list(folder.glob("*.html")))
        elif info.get("_scan_mtime") != folder_mtime:
            html_count = len(list(folder.glob("*.html")))

        complete = "yes" if (code and name) else "no"
        course_info = f"{code} - {name}" if (code and name) else "NO COURSE INFO"
        rows.append((code, folder_id, course_info, title, html_count, complete))

    for code, folder_id, course_info, title, html_count, complete in sorted(rows, key=lambda r: (r[0] == "", r[0].lower())):
        print(f"{folder_id}\t{course_info}\t{title}\t{html_count}\t{complete}")

# Prompt user to add course codes for downloads missing them
annotate:
    #!/usr/bin/env zsh
    set -e

    # Update cache first
    just _update_cache

    # Run Python script to prompt for missing courses
    uv run python add_course.py

# Serve a downloaded exam locally (interactive folder selection)
serve:
    #!/usr/bin/env zsh
    set -e

    # Update cache
    just _update_cache > /dev/null 2>&1

    if [[ ! -d "./downloads" ]]; then
        echo "Error: No downloads folder found."
        exit 1
    fi

    rows=("${(@f)$(just _downloads_rows)}")
    if [[ ${#rows[@]} -eq 0 ]]; then
        echo "Error: No downloaded exams found."
        exit 1
    fi

    missing_info=()
    for row in "${rows[@]}"; do
        IFS=$'\t' read -r folder course_info title html_count complete <<< "$row"
        if [[ "$complete" != "yes" ]]; then
            missing_info+=("$folder")
        fi
    done

    if [[ ${#missing_info[@]} -gt 0 ]]; then
        echo "Error: Some downloads are missing course information:"
        for folder in "${missing_info[@]}"; do
            echo "  - $folder"
        done
        printf "\nPlease run 'just annotate' to add course codes and names for all downloads.\n"
        exit 1
    fi

    printf '%s\n\n' "Available downloaded exams:"

    # Display numbered list
    for i in {1..${#rows[@]}}; do
        IFS=$'\t' read -r folder course_info title html_count complete <<< "${rows[$i]}"
        echo "  $i) [$course_info] $folder ${title:+— $title} ($html_count HTML files)"
    done

    printf "\nSelect exam number (or 'q' to quit): "
    read choice
    [[ "$choice" == [qQ] ]] && exit 0

    if ! [[ "$choice" =~ ^[0-9]+$ ]] || [[ "$choice" -lt 1 ]] || [[ "$choice" -gt ${#rows[@]} ]]; then
        echo "Error: Invalid selection."
        exit 1
    fi

    printf "Run python server silently? (y/n): "
    read silentt

    IFS=$'\t' read -r selected course_info title html_count complete <<< "${rows[$choice]}"
    printf '\nStarting server for: %s%s\n' "$selected" "${title:+ — $title}"
    printf '%s\n\n' "Open http://localhost:8081/overview.html"
    
    # Define a Python script that forces no-cache headers
    py_server="
    import http.server
    class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            super().end_headers()
    http.server.HTTPServer(('', 8081), NoCacheHandler).serve_forever()
    "

    # Run the custom python script instead of the module
    if [[ "$silentt" == "n" || "$silentt" == "N" ]]; then
        cd "./downloads/$selected" && uv run python -c "$py_server"
    else
        cd "./downloads/$selected" && uv run python -c "$py_server" > /dev/null 2>&1
    fi

# Serve a specific exam by assignment ID
serve-id ID:
    #!/usr/bin/env zsh
    if [[ ! -d "./downloads/{{ID}}" ]]; then
        echo "Error: Exam {{ID}} not found."
        exit 1
    fi
    title=$(just _get_title "./downloads/{{ID}}")
    echo "Starting server for: {{ID}} ${title:+— $title}"
    cd "./downloads/{{ID}}" && uv run python -m http.server 8081

# List all downloaded exams
list:
    #!/usr/bin/env zsh
    [[ ! -d "./downloads" ]] && echo "No downloads found." && exit 0

    # Update cache
    just _update_cache > /dev/null 2>&1

    printf '%s\n\n' "Downloaded exams:"
    rows=("${(@f)$(just _downloads_rows)}")
    for row in "${rows[@]}"; do
        IFS=$'\t' read -r id course_info title html_count complete <<< "$row"
        echo "  - [$course_info] $id ${title:+— $title} ($html_count HTML files)"
    done

install:
    uv sync

lint:
    uv run ruff format .
    uv run ruff check --fix .
