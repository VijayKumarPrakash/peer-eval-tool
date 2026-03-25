# MS 114 Peer Evaluation Parser

Batch parser for MEDIAST 114 peer evaluation PDFs with section-level aggregation and qualitative summarization. Automatically extracts scores, per-metric comments, and free-text feedback, then optionally synthesizes qualitative feedback per discussion leader group using the Claude API.


## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Create your rosters
The `rosters.py` file contains student enrollment by section. It is **NOT committed** to the repository for privacy.

Copy the template and populate with your rosters:
```bash
cp rosters_template.py rosters.py
```

Then edit `rosters.py` with your actual section rosters:
```python
SECTION_105 = {
    "lastname",  # Student Name
    "anotherlasstname",
}

SECTION_106 = {
    "morestudents",
}
```

### 3. Set up your API key (for qualitative summaries)

Copy the example env file and add your Anthropic API key:
```bash
cp .env.example .env
# then edit .env and paste your key
```

`.env` is gitignored and will never be committed.

## Usage

Parse all evaluations for a specific section:
```bash
python parse_eval.py --path /path/to/evaluation/folder --section 105
```

Add `--summarize` to also generate a qualitative prose summary per discussion leader group:
```bash
python parse_eval.py --path /path/to/evaluation/folder --section 105 --summarize
```

**Arguments:**
- `--path`: Directory containing PDF evaluation files
- `--section`: Section number (105 or 106)
- `--summarize`: Call the Claude API to generate a qualitative summary per DL group (requires `ANTHROPIC_API_KEY` in `.env`)

**Output:**
- Lists processed files (✓), skipped blank files (⊘), and errors (✗)
- Section-level statistics: count, mean, stdev, min/max per metric
- Automatically skips blank/unfilled submissions
- With `--summarize`: a prose summary per discussion leader topic, drawing on good points, murky points, and any per-metric comments students wrote beneath individual scale rows

## Privacy

The following are ignored by git (see `.gitignore`):
- `rosters.py` — Student roster with names
- `dl_group_*/` — Source evaluation files with student responses
- `submissions/` — Downloaded PDF submissions folder
- `.env` — API key

These files should remain local only.
