# Universal Outreach Automator

Streamlit app for hotel discovery + outreach email sending, with AI-generated custom notes, CSV/XLSX workflow, and reusable CV attachments.

## Usage of AI
This project was generated with AI, mostly by:
- Copilot VS Code Extension
- Gemini 

## What This Project Does

The app has 3 modules:

1. City Generator
- Tracks used vs unused cities from `cities.txt` and `discovery_tracker.json`.

2. Discovery
- Searches hospitality targets (Google Places).
- Crawls websites for contact emails.
- Generates one personalized `custom_note` per row using Gemini.
- Shows Gemini diagnostics (AI vs fallback counts, fallback reasons, model attempts).

3. Outreach
- Sends HTML-first personalized emails from discovered/uploaded data.
- Includes plain-text fallback part in MIME.
- Supports dry run preview mode and attachment checks.

## Current Project Layout

```text
.
├── app.py
├── example.py
├── requirements.txt
├── .env.example
├── README.md
├── cities.txt
├── discovery_tracker.json
├── sent_emails_registry.json
├── assets/
├── cvs/
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── core_logic.py
│   ├── data_loader.py
│   ├── emailer.py
│   ├── templates.py
│   └── ui.py
└── tests/
        ├── test_logic.py
        └── test_gemini_note.py
```

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

## Run

```bash
streamlit run app.py
```

## Environment Variables

Main values loaded from `.env`:

```env
EMAIL_USER=your_email@gmail.com
EMAIL_PASSWORD=your_app_password

SENDER_NAME=Your Name
SENDER_PHONE=+421...
LINKEDIN_URL=https://www.linkedin.com/in/your-profile/
LINKEDIN_LABEL=LinkedIn Profile

MAPS_API_KEY=...
GEMINI_API_KEY=...

BUSINESS_TYPE=Hotels
COUNTRY=Norway
SEARCH_QUERY={business_type} in {city}, {country}
ATTACHMENTS=cv_file_1.pdf,cv_file_2.pdf
TRASH_EMAIL_PATTERNS=
```

Notes:
- `EMAIL_PASSWORD` is required only for real sending (`Dry Run` off).
- `ATTACHMENTS` should match file names present in `cvs/`.

## Data Expectations

Discovery output and outreach input should contain (at minimum):

- `email`
- `target_company`
- `target_city`
- `custom_note`

You can still use custom templates with additional placeholders, but columns must exist in the file.

## Gemini Behavior (Discovery)

Gemini note generation is integrated in `app.py` and includes:

- strict single-sentence prompting
- anti-list formatting constraints
- sentence normalization/cleanup
- retry when output is incomplete
- fallback note with diagnostics if generation fails

Use Discovery -> `Gemini Note Diagnostics` to inspect:

- AI vs fallback counts
- fallback reasons
- discovered/attempted Gemini models

Use `Test Gemini note generation` before full discovery runs.

## Email Sending

Outreach is HTML-first:

- main email body is HTML template
- plain-text fallback is included for email clients that do not render HTML
- dry run previews each generated message without SMTP send

Real sending uses SMTP SSL (`smtp.gmail.com:465`).

## CLI Example Script

`example.py` is an optional CLI helper for non-UI runs.

Dry run:

```bash
python example.py --input ready_to_send.csv
```

Real send:

```bash
python example.py --input ready_to_send.xlsx --send
```

With attachments:

```bash
python example.py --input ready_to_send.xlsx --send \
    --attachment cvs/CV_Michal_Stanislav_Malik.pdf \
    --attachment cvs/CV_Natalia_Hudecova.pdf
```

## Tests

Run focused Gemini tests:

```bash
python -m pytest tests/test_gemini_note.py
```

Current tests cover:

- AI success path
- fallback path
- failure handling
- per-company generation call behavior

## Important Files

- `app.py`: primary Streamlit app and workflow orchestration
- `src/templates.py`: editable defaults and campaign text
- `src/core_logic.py`: deduplication, validation, registry operations
- `tests/test_gemini_note.py`: Gemini unit tests
- `cvs/README.md`: attachment folder rules

## Troubleshooting

1. Gemini always fallbacks
- verify `GEMINI_API_KEY`
- use `Test Gemini note generation`
- inspect Discovery diagnostics for model errors

2. Emails not sending
- verify `EMAIL_USER` + `EMAIL_PASSWORD`
- keep `Dry Run` off only when credentials are valid

3. Missing attachments
- ensure file names in `ATTACHMENTS` exactly match files in `cvs/`

4. Invalid template placeholders
- make sure every placeholder key exists as a column in your input dataframe
