# ANS Exam Downloader

Download exams and answers from [ANS](https://ans.app) for offline viewing with full replication, including images, fonts, and questions navigation.

## Requirements

- Python 3.12+
- [just](https://github.com/casey/just) (optional, for shortcut)

## Setup

### 1. Install dependencies

```bash
uv sync

# or with pip/venv
pip install .
```

## Usage

### With `just` (recommended)

```bash
# Show available commands
just

# Download an exam
just download 'https://ans.app/universities/15/courses/569592/assignments/1492373/grading/view/495117398'

# Add course codes for downloads missing them
# If you redownload an assignment, no need doing annotate again
just annotate

# List downloaded exams
just list

# Open an exam locally (interactive selection)
just serve

# Serve a specific exam by ID
just serve-id 1492373
```

Note: This repo uses a small metadata cache file, `.ans_cache.json`, to keep `just list`/`just serve` fast. It is automatically updated by the `just` commands and can be committed.

### Without `just`

```bash
# with uv
uv run python main.py
# or without uv
python main.py

# Open locally
cd downloads/<assignment_id>
uv run python -m http.server 8081
```

Then open http://localhost:8081/overview.html in your browser.

Technically, you can also open any HTML file directly in your browser. Most features work, but some resources may not load due to browser security restrictions.

## License

MIT
