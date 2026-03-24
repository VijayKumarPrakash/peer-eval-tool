# MS 114 Peer Evaluation Parser

Batch parser for MEDIAST 114 peer evaluation PDFs with section-level aggregation. This code automatically parses peer evaluation forms and summarize scores and qualitative feedback from students.


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

## Usage

Parse all evaluations for a specific section:
```bash
python parse_eval.py --path /path/to/evaluation/folder --section 105
```

**Arguments:**
- `--path`: Directory containing PDF evaluation files
- `--section`: Section number (105 or 106)

**Output:**
- Lists processed files (✓), skipped blank files (⊘), and errors (✗)
- Section-level statistics: count, mean, stdev, min/max per metric
- Automatically skips blank/unfilled submissions

## Privacy

The following are ignored by git (see `.gitignore`):
- `rosters.py` — Student roster with names
- `dl_group_*/` — Source evaluation files with student responses
- `submissions/` — Downloaded PDF submissions folder

These files should remain local only.
