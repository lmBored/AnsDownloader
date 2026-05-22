#!/usr/bin/env python3
import contextlib
import html
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

BASE_URL = "https://ans.app"
DOWNLOAD_DIR = Path(__file__).resolve().parent / "downloads"
PDF_SUBDIR = "pdfs"
PDFJS_BUILD_FILES = ("pdf.worker.mjs", "pdf.sandbox.mjs")
PDFJS_DEFAULT_LOCALE = "en-US"

# URL components extracted from exam URL (set during download)
URL_COMPONENTS: dict[str, str] = {}


def extract_assignment_id(url: str) -> str:
    # URL format: .../assignments/1492373/grading/...
    match = re.search(r"/assignments/(\d+)/", url)
    if match:
        return match.group(1)
    msg = f"Could not extract assignment ID from URL: {url}"
    raise ValueError(msg)


def extract_url_components(url: str) -> dict[str, str]:
    # URL format: https://ans.app/universities/15/courses/569592/assignments/1492373/...
    match = re.match(
        r"https://ans\.app/universities/(\d+)/courses/(\d+)/assignments/(\d+)/",
        url,
    )
    if not match:
        msg = f"Could not extract URL components from: {url}"
        raise ValueError(msg)

    return {
        "university_id": match.group(1),
        "course_id": match.group(2),
        "assignment_id": match.group(3),
    }


def fetch_page(session: requests.Session, url: str) -> str:
    """Fetch a page and return its HTML content."""
    print(f"  Fetching: {url}")
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def extract_question_links(html: str, exam_url: str) -> list[dict]:
    """
    Extract all question links from the page.
    """
    soup = BeautifulSoup(html, "html.parser")
    questions = []

    # Extract base URL pattern from exam_url
    # e.g., https://ans.app/universities/15/courses/569592/assignments/1492373/grading/view/495117398
    # -> base: https://ans.app/universities/15/courses/569592/assignments/1492373/grading/view/
    match = re.match(
        r"(https://ans\.app/universities/\d+/courses/\d+/assignments/\d+/grading/(?:view|review)/)", exam_url
    )
    if not match:
        msg = f"Could not extract base URL pattern from: {exam_url}"
        raise ValueError(msg)
    base_view_url = match.group(1)

    # Find all anchor tags with data-submission-id attribute
    for link in soup.find_all("a", attrs={"data-submission-id": True}):
        submission_id = link.get("data-submission-id")

        if not submission_id:
            continue

        # Extract question number from the span inside
        question_span = link.find("span", class_="question-button")
        question_num = "unknown"
        if question_span:
            question_num = question_span.get_text(strip=True)

        # Build the correct view URL with ?nav=result
        full_url = f"{base_view_url}{submission_id}?nav=result"

        questions.append(
            {
                "submission_id": submission_id,
                "question_number": question_num,
                "url": full_url,
            }
        )

    return questions


def download_resource(session: requests.Session, url: str, save_path: Path) -> bool:
    """Download a resource (image, CSS, etc.) and save it locally."""
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(response.content)
        return True
    except Exception as e:
        print(f"    Warning: Failed to download {url[:80]}...: {e}")
        return False


def get_resource_local_path(url: str, resources_dir: Path) -> Path:
    """Generate a local path for a resource URL."""
    parsed = urlparse(url)
    path = unquote(parsed.path).lstrip("/")

    # Handle empty paths or just domain
    if not path:
        path = "index"

    # For external domains, include a short domain prefix to avoid collisions
    domain = parsed.netloc
    if domain and "ans.app" not in domain and "assets.ans.app" not in domain:
        # Create domain-specific subfolder for external resources
        domain_folder = domain.replace(".", "_")
        path = f"{domain_folder}/{path}"

    # Add query string hash to filename if present (for cache-busted assets)
    if parsed.query:
        # Create a short hash of the query string
        query_hash = str(hash(parsed.query))[-8:]
        name, ext = os.path.splitext(path)
        # If no extension, try to guess from path
        if not ext:
            ext = ".bin"
        path = f"{name}_{query_hash}{ext}"

    return resources_dir / path


def is_pdf_url(url: str) -> bool:
    """Return True if the URL points to a PDF file."""
    return bool(re.search(r"\.pdf($|[?#])", url, flags=re.IGNORECASE))


def extract_pdfjs_file_url(viewer_url: str) -> str | None:
    """Extract the file parameter from a pdfjs viewer URL, if present."""
    parsed = urlparse(viewer_url)
    path = parsed.path or ""
    if "pdfjs" not in path or "viewer" not in path:
        return None

    file_url = None
    params = parse_qs(parsed.query)
    if params.get("file"):
        file_url = params["file"][0]

    if not file_url and parsed.fragment:
        fragment = parsed.fragment
        fragment_query = fragment.split("?", 1)[1] if "?" in fragment else fragment
        frag_params = parse_qs(fragment_query)
        if frag_params.get("file"):
            file_url = frag_params["file"][0]

    if not file_url:
        return None

    return unquote(html.unescape(file_url))


def get_pdf_local_path(pdf_url: str, resources_dir: Path) -> Path:
    """Generate a local path for a PDF URL."""
    parsed = urlparse(pdf_url)
    filename = os.path.basename(unquote(parsed.path)) or "document.pdf"
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"

    # Add a short hash if query/fragment present to reduce collisions
    if parsed.query or parsed.fragment:
        name, ext = os.path.splitext(filename)
        hash_suffix = str(hash(f"{parsed.query}|{parsed.fragment}"))[-8:]
        filename = f"{name}_{hash_suffix}{ext}"

    return resources_dir / PDF_SUBDIR / filename


def ensure_pdfjs_runtime_assets(session: requests.Session, resources_dir: Path) -> None:
    """Ensure PDF.js worker/sandbox and locale assets exist for offline viewing."""
    assignment_root = resources_dir.parent
    pdfjs_root = assignment_root / "pdfjs"

    for filename in PDFJS_BUILD_FILES:
        local_path = pdfjs_root / "build" / filename
        if not local_path.exists():
            download_resource(session, f"{BASE_URL}/pdfjs/build/{filename}", local_path)

    locale_dir = resources_dir / "pdfjs" / "web" / "locale" / PDFJS_DEFAULT_LOCALE
    locale_file = locale_dir / "viewer.ftl"
    if not locale_file.exists():
        download_resource(
            session,
            f"{BASE_URL}/pdfjs/web/locale/{PDFJS_DEFAULT_LOCALE}/viewer.ftl",
            locale_file,
        )

    locale_index = resources_dir / "pdfjs" / "web" / "locale" / "locale.json"
    if not locale_index.exists():
        download_resource(
            session,
            f"{BASE_URL}/pdfjs/web/locale/locale.json",
            locale_index,
        )


def patch_pdfjs_viewer_asset_paths(resources_dir: Path) -> None:
    """Patch PDF.js viewer runtime paths for subdirectory serving."""
    assets_dir = resources_dir / "assets"
    if not assets_dir.exists():
        return

    # From `resources/assets/*` up to the assignment root.
    worker_rel = "../../pdfjs/build/pdf.worker.mjs"
    sandbox_rel = "../../pdfjs/build/pdf.sandbox.mjs"

    worker_pattern = re.compile(r"([\"'])[^\"']*pdfjs/build/pdf\.worker\.mjs\1")
    sandbox_pattern = re.compile(r"([\"'])[^\"']*pdfjs/build/pdf\.sandbox\.mjs\1")

    for js_path in assets_dir.glob("pdf-viewer-*.js"):
        try:
            original = js_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        patched = worker_pattern.sub(rf"\1{worker_rel}\1", original)
        patched = sandbox_pattern.sub(rf"\1{sandbox_rel}\1", patched)

        if patched != original:
            with contextlib.suppress(OSError):
                js_path.write_text(patched, encoding="utf-8")


def find_pdf_url_for_viewer(tag: Tag, soup: BeautifulSoup) -> str | None:
    """Find a PDF URL in `data-url` attributes near the PDF.js viewer iframe."""

    def find_in(scope: Tag | BeautifulSoup) -> str | None:
        for el in scope.find_all(attrs={"data-url": True}):
            raw = el.get("data-url")
            if not raw:
                continue
            candidate = html.unescape(str(raw))
            if is_pdf_url(candidate):
                return candidate
        return None

    for parent in tag.parents:
        if hasattr(parent, "find_all"):
            found = find_in(parent)
            if found:
                return found

    return find_in(soup)


def disable_ans_pdf_panel_js_for_offline_view(tag: Tag) -> None:
    """Disable ANS' PDF panel controller so offline PDF.js iframes keep working."""
    for parent in getattr(tag, "parents", []) or []:
        if getattr(parent, "attrs", None) and "data-js-pdf-panel" in parent.attrs:
            del parent.attrs["data-js-pdf-panel"]
            break


def strip_ans_pdf_panel_hooks(soup: BeautifulSoup) -> int:
    """
    Remove ANS' PDF panel hook attributes across the document.

    The ANS frontend attaches behavior to `[data-js-pdf-panel]` which will attempt
    to load presigned/online URLs and can replace our local PDF.js iframe.
    Removing the hook makes offline rendering stable.
    """
    removed = 0
    for el in soup.select("[data-js-pdf-panel]"):
        if "data-js-pdf-panel" in getattr(el, "attrs", {}):
            del el.attrs["data-js-pdf-panel"]
            removed += 1
    return removed


def process_pdf_embeds(
    session: requests.Session,
    soup: BeautifulSoup,
    resources_dir: Path,
    page_url: str,
    save_path: Path,
) -> int:
    """Download embedded PDFs (pdfjs viewer or direct embeds) and rewrite URLs."""
    pdf_attrs = [
        ("iframe", "src"),
        ("iframe", "data-src"),
        ("embed", "src"),
        ("embed", "data-src"),
        ("object", "data"),
        ("object", "data-src"),
    ]

    rewritten = 0

    for tag_name, attr in pdf_attrs:
        for tag in soup.find_all(tag_name):
            raw_url = tag.get(attr)
            if not raw_url:
                continue

            url = (
                raw_url
                if isinstance(raw_url, str)
                else " ".join(raw_url)
                if isinstance(raw_url, list)
                else str(raw_url)
            )

            if url.startswith(("data:", "#", "javascript:")):
                continue

            url = html.unescape(url)
            absolute_url = urljoin(page_url, url)

            parsed_url = urlparse(absolute_url)
            is_viewer_url = "pdfjs" in (parsed_url.path or "") and "viewer" in (parsed_url.path or "")

            pdf_url = extract_pdfjs_file_url(absolute_url)
            is_viewer = False
            if pdf_url:
                is_viewer = True
            elif is_viewer_url:
                pdf_url = find_pdf_url_for_viewer(tag, soup)
                if not pdf_url:
                    continue
                is_viewer = True
            elif is_pdf_url(absolute_url):
                pdf_url = absolute_url
            else:
                continue

            if is_viewer:
                absolute_pdf_url = urljoin(absolute_url, pdf_url)
            else:
                absolute_pdf_url = absolute_url

            local_pdf_path = get_pdf_local_path(absolute_pdf_url, resources_dir)
            if not download_resource(session, absolute_pdf_url, local_pdf_path):
                continue

            if is_viewer:
                ensure_pdfjs_runtime_assets(session, resources_dir)
                viewer_local_path = get_resource_local_path(absolute_url, resources_dir)
                if viewer_local_path.suffix == ".bin":
                    viewer_local_path = viewer_local_path.with_suffix(".html")
                if not viewer_local_path.exists():
                    viewer_html = fetch_page(session, absolute_url)
                    process_and_save_html(
                        session,
                        viewer_html,
                        viewer_local_path,
                        resources_dir,
                        absolute_url,
                    )

                viewer_relative = os.path.relpath(viewer_local_path, save_path.parent)
                pdf_relative_to_viewer = os.path.relpath(local_pdf_path, viewer_local_path.parent)
                tag[attr] = f"{viewer_relative}?file={quote(pdf_relative_to_viewer)}"

                # Prevent ANS' frontend JS from overriding the local iframe with
                # presigned URLs (which fails offline and shows the "Retry" button).
                disable_ans_pdf_panel_js_for_offline_view(tag)
            else:
                relative_path = os.path.relpath(local_pdf_path, save_path.parent)
                tag[attr] = relative_path

            rewritten += 1

    return rewritten


def process_css_file(
    session: requests.Session,
    css_path: Path,
    resources_dir: Path,
    css_url: str,
) -> None:
    """
    Process a downloaded CSS file to download referenced resources and rewrite URLs.
    """
    try:
        css_content = css_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"    Warning: Could not read CSS file {css_path}: {e}")
        return

    # Find all url() references in CSS
    # Matches: url(/path), url("/path"), url('/path'), url(../path), etc.
    url_pattern = re.compile(r'url\(["\']?([^)"\']+)["\']?\)')

    urls_found = url_pattern.findall(css_content)
    if not urls_found:
        return

    print(f"    Processing CSS: found {len(urls_found)} url() references")

    # Track replacements to make
    replacements = {}

    for url in urls_found:
        # Skip data URLs
        if url.startswith("data:"):
            continue

        # Skip already processed relative paths
        if url.startswith(("./", "../")):
            continue

        # Unescape any HTML entities
        url_clean = html.unescape(url.strip())

        # Build absolute URL
        # For absolute paths like /assets/..., use https://assets.ans.app as base
        if url_clean.startswith("/"):
            absolute_url = f"https://assets.ans.app{url_clean}"
        else:
            # For relative paths, resolve against the CSS URL
            absolute_url = urljoin(css_url, url_clean)

        # Get local path for the resource
        local_path = get_resource_local_path(absolute_url, resources_dir)

        # Download the resource if not already downloaded
        if not local_path.exists() and download_resource(session, absolute_url, local_path):
            print(f"      Downloaded: {url_clean[:60]}...")

        # Calculate relative path from CSS file to the downloaded resource
        if local_path.exists():
            relative_path = os.path.relpath(local_path, css_path.parent)
            replacements[url] = relative_path

    # Apply all replacements to CSS content
    modified_css = css_content
    for original_url, new_path in replacements.items():
        # Replace all variations: url(/path), url("/path"), url('/path')
        # We need to be careful to replace the exact match
        patterns = [
            f"url({original_url})",
            f'url("{original_url}")',
            f"url('{original_url}')",
        ]
        for pattern in patterns:
            if pattern in modified_css:
                modified_css = modified_css.replace(pattern, f'url("{new_path}")')

    # Save the modified CSS
    try:
        css_path.write_text(modified_css, encoding="utf-8")
        print(f"    Updated CSS with {len(replacements)} local paths")
    except Exception as e:
        print(f"    Warning: Could not save modified CSS {css_path}: {e}")


def process_and_save_html(
    session: requests.Session,
    html_content: str,
    save_path: Path,
    resources_dir: Path,
    page_url: str,
) -> None:
    """
    Process HTML to download resources and update links to local paths.
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # Disable Turbo/remote interception for question navigation links so local files load normally
    for link in soup.find_all("a", attrs={"data-submission-id": True}):
        link.attrs.pop("data-remote", None)
        link["data-turbo"] = "false"
        href = link.get("href")
        if href and "question_" in href:
            link["href"] = href.split("?", 1)[0]

    # Download and rewrite embedded PDFs (pdfjs viewer or direct embeds)
    process_pdf_embeds(session, soup, resources_dir, page_url, save_path)

    # Even if we couldn't rewrite a particular viewer iframe (e.g., when reprocessing
    # already-downloaded HTML), strip ANS' hook so the page doesn't show "Retry".
    strip_ans_pdf_panel_hooks(soup)

    # Resource attributes to process
    resource_attrs = [
        ("img", "src"),
        ("img", "data-src"),  # Lazy-loaded images
        ("script", "src"),
        ("source", "src"),
        ("source", "data-src"),
        ("video", "src"),
        ("video", "data-src"),
        ("audio", "src"),
    ]

    # Domains to skip (external CDNs that provide standard libraries)
    skip_domains = {"gstatic.com", "cloudflare.com", "google.com", "googleapis.com"}

    # Process link tags separately to handle CSS files specially
    for tag in soup.find_all("link"):
        url = tag.get("href")
        if not url:
            continue

        # Skip data URLs, anchors, and javascript
        if url.startswith(("data:", "#", "javascript:")):
            continue

        # Unescape HTML entities in URL (e.g., &amp; -> &)
        url = html.unescape(url)

        # Build absolute URL
        absolute_url = urljoin(page_url, url)
        parsed = urlparse(absolute_url)

        # Skip external standard CDNs
        if any(skip in parsed.netloc for skip in skip_domains):
            continue

        # Get local path for resource
        local_path = get_resource_local_path(absolute_url, resources_dir)

        # Download the resource
        if download_resource(session, absolute_url, local_path):
            # Update the HTML to use relative path
            relative_path = os.path.relpath(local_path, save_path.parent)
            tag["href"] = relative_path

            # If this is a stylesheet, process it to download fonts and other CSS resources
            rel = tag.get("rel", [])
            is_stylesheet = "stylesheet" in rel if isinstance(rel, list) else rel == "stylesheet"

            if is_stylesheet and local_path.suffix == ".css":
                process_css_file(session, local_path, resources_dir, absolute_url)

    for tag_name, attr in resource_attrs:
        for tag in soup.find_all(tag_name):
            url = tag.get(attr)
            if not url:
                continue

            # Skip data URLs, anchors, and javascript
            if url.startswith(("data:", "#", "javascript:")):
                continue

            # Unescape HTML entities in URL (e.g., &amp; -> &)
            url = html.unescape(url)

            # Build absolute URL
            absolute_url = urljoin(page_url, url)
            parsed = urlparse(absolute_url)

            # Skip external standard CDNs
            if any(skip in parsed.netloc for skip in skip_domains):
                continue

            # Get local path for resource
            local_path = get_resource_local_path(absolute_url, resources_dir)

            # Download the resource
            if download_resource(session, absolute_url, local_path):
                # Update the HTML to use relative path
                relative_path = os.path.relpath(local_path, save_path.parent)
                tag[attr] = relative_path

    # Also handle inline styles with url()
    for tag in soup.find_all(style=True):
        style = tag["style"]
        urls = re.findall(r'url\(["\']?([^)"\']+)["\']?\)', style)
        for url in urls:
            if url.startswith(("data:", "#")):
                continue
            url_unescaped = html.unescape(url)
            absolute_url = urljoin(page_url, url_unescaped)
            parsed = urlparse(absolute_url)
            if any(skip in parsed.netloc for skip in skip_domains):
                continue
            local_path = get_resource_local_path(absolute_url, resources_dir)
            if download_resource(session, absolute_url, local_path):
                relative_path = os.path.relpath(local_path, save_path.parent)
                tag["style"] = style.replace(url, relative_path)

    # Patch the bundled PDF.js viewer runtime to use relative worker/sandbox
    # paths, so it works even when the assignment is served from a subdirectory.
    patch_pdfjs_viewer_asset_paths(resources_dir)

    # Save the modified HTML
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_text(str(soup), encoding="utf-8")


def build_submission_mapping(overview_path: Path) -> dict[str, str]:
    """
    Build mapping from submission_id to question number.
    Parses the overview HTML to find question buttons with their submission IDs.
    """
    content = overview_path.read_text()
    soup = BeautifulSoup(content, "html.parser")
    mapping: dict[str, str] = {}

    for link in soup.find_all("a", attrs={"data-submission-id": True}):
        submission_id = link.get("data-submission-id")
        if not submission_id:
            continue

        question_span = link.find("span", class_="question-button")
        if not question_span:
            continue

        question_num = question_span.get_text(strip=True)
        if not question_num:
            continue

        mapping[submission_id] = question_num

    return mapping


def fix_go_to_links(html_file: Path, url_components: dict[str, str], mapping: dict[str, str]) -> int:
    """
    Replace all navigation links using the submission_id to question mapping.
    Handles both /go_to/ and /grading/(?:view|review)/ URL patterns.
    Returns count of links fixed.
    """
    content = html_file.read_text()
    original = content
    fixes = 0

    uni_id = url_components["university_id"]
    course_id = url_components["course_id"]
    assignment_id = url_components["assignment_id"]

    # Replace go_to URLs with local question files
    for submission_id, question_num in mapping.items():
        # Pattern 1: /go_to/{submission_id}
        old_go_to = (
            f"/universities/{uni_id}/courses/{course_id}/assignments/{assignment_id}/grading/go_to/{submission_id}"
        )
        new_href = f"question_{question_num}.html"
        if old_go_to in content:
            content = content.replace(old_go_to, new_href)
            fixes += 1

        # Pattern 2: /grading/view/{submission_id} (used by prev/next navigation)
        # Handle both with and without query params
        old_view = (
            f"/universities/{uni_id}/courses/{course_id}/assignments/{assignment_id}/grading/view/{submission_id}"
        )
        old_review = (
            f"/universities/{uni_id}/courses/{course_id}/assignments/{assignment_id}/grading/review/{submission_id}"
        )
        for old_path in (old_view, old_review):
            if old_path in content:
                # Replace full URL (https://ans.app/...)
                full_old_path = f"https://ans.app{old_path}"
                content = content.replace(full_old_path, new_href)
                # Also replace relative paths
                content = content.replace(old_path, new_href)
                fixes += 1

    if content != original:
        html_file.write_text(content)

    return fixes


def sanitize_offline_navigation(html_file: Path) -> int:
    """
    Remove remote/turbo attributes from local question links so navigation works offline.
    Returns count of attributes removed.
    """
    try:
        soup = BeautifulSoup(html_file.read_text(), "html.parser")
    except Exception:
        return 0

    removed = 0
    changed = False

    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        if not href.startswith("question_"):
            continue

        if "?" in href:
            clean_href = href.split("?", 1)[0]
            if clean_href != href:
                link["href"] = clean_href
                changed = True

        for attr in (
            "data-remote",
            "data-js-pagination-item",
            "data-js-indicator",
            "data-disable-with",
            "data-submission-id",
        ):
            if attr in link.attrs:
                del link[attr]
                removed += 1
                changed = True

        # Explicitly disable turbo if present
        if link.get("data-turbo") not in (None, "false"):
            link["data-turbo"] = "false"
            changed = True

    if changed:
        html_file.write_text(str(soup))

    return removed


def fix_navigation(output_dir: Path, url_components: dict[str, str]) -> None:
    """
    Fix navigation links in all downloaded HTML files.
    Converts go_to links to local question file references.
    """
    print("\n[4/5] Fixing navigation links...")

    overview = output_dir / "overview.html"
    if not overview.exists():
        print("  Warning: overview.html not found, skipping navigation fix")
        return

    mapping = build_submission_mapping(overview)
    if not mapping:
        print("  Warning: Could not build submission mapping")
    else:
        print(f"  Built mapping for {len(mapping)} questions")

    html_files = list(output_dir.glob("*.html"))
    total_fixes = 0

    for html_file in html_files:
        if mapping:
            fixes = fix_go_to_links(html_file, url_components, mapping)
            if fixes > 0:
                print(f"    Fixed {fixes} links in {html_file.name}")
                total_fixes += fixes

        removed = sanitize_offline_navigation(html_file)
        if removed > 0:
            print(f"    Removed {removed} remote nav attributes in {html_file.name}")

    print(f"  Total: Fixed {total_fixes} navigation links")


KATEX_FONT_PATTERN = re.compile(r"KaTeX_[A-Za-z0-9_-]+(?:\.(?:woff2|woff|ttf|otf|eot))?")


def detect_katex_fonts_in_asset(asset_path: Path) -> set[str]:
    """Return KaTeX font filenames referenced in a text asset file."""
    try:
        asset_content = asset_path.read_text(encoding="utf-8")
    except Exception:
        return set()

    return set(KATEX_FONT_PATTERN.findall(asset_content))


def auto_download_katex_fonts(resources_dir: Path) -> tuple[int, list[tuple[str, Path]]]:
    """
    Auto-download KaTeX fonts referenced in CSS into resources/assets/fonts.
    Returns (downloaded_count, missing_list).
    """
    asset_files = list(resources_dir.rglob("*.css")) + list(resources_dir.rglob("*.js"))
    if not asset_files:
        return 0, []

    font_names: set[str] = set()
    for asset_file in asset_files:
        font_names.update(detect_katex_fonts_in_asset(asset_file))

    if not font_names:
        return 0, []

    fonts_dir = resources_dir / "assets" / "fonts"
    downloaded = 0
    missing: list[tuple[str, Path]] = []

    for name in sorted(font_names):
        if "-" not in name:
            continue
        filename = name if "." in name else f"{name}.woff2"
        local_path = fonts_dir / filename
        if local_path.exists():
            continue

        if try_auto_download_font(filename, local_path):
            downloaded += 1
        else:
            missing.append((filename, local_path))

    return downloaded, missing


def detect_missing_fonts_in_css(css_path: Path, resources_dir: Path) -> list[tuple[str, Path]]:
    """
    Detect missing font/asset references in a CSS file.
    Returns list of tuples: (original_url, expected_local_path)
    """
    missing = []

    try:
        css_content = css_path.read_text(encoding="utf-8")
    except Exception:
        return missing

    # Find all url() references
    url_pattern = re.compile(r'url\(["\']?([^)"\']+)["\']?\)')
    urls = url_pattern.findall(css_content)

    for url in urls:
        url_raw = html.unescape(url.strip())

        # Skip empty, data URLs, anchors, javascript, and CSS variables
        if not url_raw or url_raw.startswith(("data:", "#", "javascript:")):
            continue
        if "var(" in url_raw:
            continue

        parsed = urlparse(url_raw)

        # Skip external and protocol-relative URLs
        if parsed.scheme in ("http", "https") or parsed.netloc:
            continue

        url_path = parsed.path
        if not url_path:
            continue

        # Check if the referenced file exists
        # CSS urls are relative to the CSS file unless root-relative
        if url_path.startswith("/"):
            local_path = resources_dir / url_path.lstrip("/")
        else:
            local_path = (css_path.parent / url_path).resolve()

        if not local_path.exists():
            missing.append((url_raw, local_path))

    return missing


# Known CDN patterns and their base URLs for auto-downloading
KNOWN_FONT_CDNS = {
    "KaTeX_": "https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/fonts/",
    "material-icons": "https://fonts.gstatic.com/s/materialiconsoutlined/v109/",
}


def try_auto_download_font(
    url: str,
    local_path: Path,
    session: requests.Session | None = None,
) -> bool:
    """
    Attempt to auto-download a font from known CDN sources.
    Returns True if download succeeded, False otherwise.
    """
    filename = local_path.name

    # Check if this matches known font patterns
    cdn_url = None

    for pattern, base_url in KNOWN_FONT_CDNS.items():
        if pattern in filename:
            cdn_url = f"{base_url}{filename}"
            break

    if not cdn_url:
        return False

    # Try to download
    try:
        if session is None:
            session = requests.Session()

        print(f"    Auto-downloading: {filename}")
        response = session.get(cdn_url, timeout=30)
        response.raise_for_status()

        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(response.content)
        return True
    except Exception as e:
        print(f"    Warning: Could not auto-download {filename}: {e}")
        return False


def check_and_report_missing_assets(output_dir: Path) -> list[tuple[str, Path]]:
    """
    Scan all CSS files for missing assets, auto-download known fonts, and report.
    Returns list of remaining missing assets (url, expected_path).
    """
    resources_dir = output_dir / "resources"
    css_files = list(resources_dir.rglob("*.css"))

    all_missing = []

    for css_file in css_files:
        missing = detect_missing_fonts_in_css(css_file, resources_dir)
        all_missing.extend(missing)

    # Deduplicate by path
    seen_paths = set()
    unique_missing = []
    for url, path in all_missing:
        if path not in seen_paths:
            seen_paths.add(path)
            unique_missing.append((url, path))

    katex_downloaded, katex_missing = auto_download_katex_fonts(resources_dir)

    if not unique_missing and katex_downloaded == 0 and not katex_missing:
        return []

    # Try to auto-download known fonts
    print("\n[5/5] Checking for missing assets...")
    still_missing = []
    auto_downloaded = 0

    for url, path in unique_missing:
        if try_auto_download_font(url, path):
            auto_downloaded += 1
        else:
            still_missing.append((url, path))

    if auto_downloaded > 0:
        print(f"  Auto-downloaded {auto_downloaded} fonts from CDN")

    if katex_downloaded > 0:
        print(f"  Auto-downloaded {katex_downloaded} KaTeX fonts from CDN")

    if katex_missing:
        existing_paths = {path for _, path in still_missing}
        for name, path in katex_missing:
            if path not in existing_paths:
                still_missing.append((name, path))
                existing_paths.add(path)

    if still_missing:
        print("\n" + "=" * 50)
        print("MISSING ASSETS (could not auto-download)")
        print("=" * 50)
        print(f"Found {len(still_missing)} missing font/asset files:")
        for url, path in still_missing[:10]:  # Show first 10
            print(f"  - {url}")
            print(f"    Expected at: {path}")
        if len(still_missing) > 10:
            print(f"  ... and {len(still_missing) - 10} more")
        print("\nTo download manually, place the files in:")
        print(f"  {resources_dir.absolute()}")
        print("=" * 50)

    return still_missing


def download_exam(exam_url: str, session: requests.Session) -> None:
    """
    Download an entire exam from ANS.
    """
    global URL_COMPONENTS
    print(f"Starting download of exam: {exam_url}")

    # Extract assignment ID and URL components
    assignment_id = extract_assignment_id(exam_url)
    URL_COMPONENTS = extract_url_components(exam_url)
    print(f"Assignment ID: {assignment_id}")

    # Create output directories
    output_dir = DOWNLOAD_DIR / assignment_id
    resources_dir = output_dir / "resources"
    output_dir.mkdir(parents=True, exist_ok=True)
    resources_dir.mkdir(parents=True, exist_ok=True)

    # Fetch the initial page
    print("\n[1/5] Fetching initial page to get question list...")
    initial_html = fetch_page(session, exam_url)

    # Extract all question links
    print("\n[2/5] Extracting question links...")
    questions = extract_question_links(initial_html, exam_url)

    if not questions:
        print("Error: No questions found on the page. Ensure you loaded the final exam page.")
        sys.exit(1)

    print(f"Found {len(questions)} questions")

    # Download each question page
    print("\n[3/5] Downloading question pages and resources...")
    for i, question in enumerate(questions, 1):
        q_num = question["question_number"]
        q_url = question["url"]

        print(f"\nQuestion {i}/{len(questions)} (Q{q_num}):")

        # Fetch question page
        q_html = fetch_page(session, q_url)

        # Save with resources
        save_path = output_dir / f"question_{q_num}.html"
        print(f"  Saving to: {save_path}")
        process_and_save_html(session, q_html, save_path, resources_dir, q_url)

    # Also save the initial page (might have overview info)
    print("\nSaving initial overview page...")
    overview_path = output_dir / "overview.html"
    process_and_save_html(session, initial_html, overview_path, resources_dir, exam_url)

    # Fix navigation links
    fix_navigation(output_dir, URL_COMPONENTS)

    # Check for missing assets
    missing_assets = check_and_report_missing_assets(output_dir)

    print(f"\n{'=' * 50}")
    print("Download complete!")
    print(f"Files saved to: {output_dir.absolute()}")
    print(f"Total questions: {len(questions)}")
    if missing_assets:
        print(f"Warning: {len(missing_assets)} assets could not be downloaded")


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("run: pip install playwright")
        print("then run: playwright install chromium")
        sys.exit(1)

    print("\nLaunching browser...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(no_viewport=True)
        page = context.new_page()
        page.goto("https://ans.app")

        while True:
            print("1. Navigate to the exam page in the opened browser.")
            print("2. Once on the page, press ENTER to start downloading.")
            print("3. Enter 'q' or press Ctrl+C to quit.")

            try:
                cmd = input("Press ENTER to start downloading, or 'q' to quit: ")
            except KeyboardInterrupt:
                break

            if cmd.lower() == "q":
                break

            # Let the Playwright event loop process pending events (like URL changes)
            # that happened while the script was blocked on input()
            page.wait_for_timeout(100)

            current_url = None
            active_page = None

            all_pages = context.pages

            # Check all open tabs to find one that matches the exam URL format
            for tab in all_pages:
                print(f"  Checking tab: {tab.url}")
                if re.match(r"https://ans\.app/universities/\d+/courses/\d+/assignments/\d+", tab.url):
                    current_url = tab.url
                    active_page = tab
                    break

            if not current_url:
                open_urls = [tab.url for tab in context.pages]
                print(f"\nWarning: Could not find an exam page among {len(context.pages)} open tab(s).")
                print("Make sure you have an exam tab open.")
                print(f"Current open tabs: {open_urls}")
                continue

            print("\nSetting up authenticated session...")
            session = requests.Session()
            cookies = context.cookies()
            user_agent = active_page.evaluate("navigator.userAgent")

            cookies_set = 0
            for cookie in cookies:
                session.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=cookie.get("domain", ""),
                    path=cookie.get("path", "/")
                )
                cookies_set += 1

            session.headers.update(
                {
                    "User-Agent": user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                }
            )

            print(f"Loaded {cookies_set} cookies from the live browser session.")

            try:
                download_exam(current_url, session)
            except Exception as e:
                print(f"Error occurred during download: {e}")

        browser.close()


if __name__ == "__main__":
    main()
