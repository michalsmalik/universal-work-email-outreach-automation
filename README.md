# Universal Outreach Automator

![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Contributions Welcome](https://img.shields.io/badge/contributions-welcome-orange.svg)

A Streamlit-based application designed to automate the job application process (e.g., in the hospitality sector in Norway). It discovers target businesses via Google Maps, extracts contact emails, generates personalized AI cover notes using Google Gemini, and sends professional HTML-first outreach emails with PDF attachments.

Whole project can be runned at no-cost (with Google API for Maps free credits and Gemini free models)

> **Note on Creation:** The core architecture and logic of this project were built with the assistance of AI tools, primarily GitHub Copilot and Google Gemini.

---

## 🚀 Features at a Glance

The app consists of 3 integrated modules:

1. **City Generator**
   - Tracks used vs. unused cities from `cities.txt`.
   - Prevents you from searching the same location twice.

2. **Discovery Engine**
   - Searches for specific business types (Hotels, Camping, Gjestegård, etc.) using Google Places API.
   - Crawls business websites to extract public contact emails.
   - Uses **Gemini 1.5 Flash** to generate a unique, one-sentence compliment (`custom_note`) for each business to avoid spam-like repetition.
   - Includes a Global Email Registry (`sent_emails_registry.json`) to guarantee you never email the same company twice.

3. **Outreach Sender**
   - Sends HTML-first personalized emails.
   - Automatically includes a clickable LinkedIn icon and attaches CVs from the `/cvs` folder.
   - Built-in **Dry Run** mode to safely preview all emails before establishing an SMTP connection.

---

## 🛠️ Installation

**1. Clone the repository**
```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
cd YOUR_REPO_NAME
```

**2. Set up the virtual environment**
```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows use: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

**3. Configure Environment Variables**
```bash
cp .env.example .env
```

Open the `.env` file and fill in your credentials.

---

## ⚙️ Environment Configuration (`.env`)

Main values loaded from `.env`:

# Gmail SMTP Credentials (use App Passwords, not your main password)
```env
EMAIL_USER=your_email@gmail.com
EMAIL_PASSWORD=your_app_password

# Personal Info
SENDER_NAME=Your Name
SENDER_PHONE=+421...
LINKEDIN_URL=https://www.linkedin.com/in/your-profile/
LINKEDIN_LABEL=LinkedIn Profile

# API Keys
MAPS_API_KEY=your_google_maps_key
GEMINI_API_KEY=your_gemini_key

# Search Parameters
BUSINESS_TYPE=Hotels
COUNTRY=Norway
SEARCH_QUERY={business_type} in {city}, {country}
ATTACHMENTS=cv_file_1.pdf,cv_file_2.pdf
TRASH_EMAIL_PATTERNS=sentry,wix,noreply
```

*Note: `EMAIL_PASSWORD` is required only for real sending. `ATTACHMENTS` must exactly match the filenames in the `cvs/` folder.*

---

## 💻 Usage

Run the Streamlit UI:
```bash
streamlit run app.py
```

### Optional: CLI Mode
`example.py` is an optional CLI helper for running campaigns without the UI.

Dry run:
```bash
python example.py --input ready_to_send.csv
```

Real send with specific attachments:
```bash
python example.py --input ready_to_send.xlsx --send \
    --attachment cvs/CV_English.pdf
```

---

## 🧠 Gemini AI Behavior & Testing

Gemini note generation is strictly prompted to output single sentences and avoid list formatting.
Use **Discovery -> Gemini Note Diagnostics** in the UI to inspect API health, token limits, and fallback reasons.

Run focused unit tests using pytest:
```bash
python -m pytest tests/test_gemini_note.py
```

---

## ⚠️ Disclaimer & Best Practices (Please Read)

- **Rate Limits:** The free tier of the Gemini API has strict rate limits (typically 15 Requests Per Minute). The script includes a `time.sleep(4)` throttle in the discovery loop to prevent crashes. Be patient during large discoveries.
- **Gmail SMTP:** Sending hundreds of emails rapidly via `smtp.gmail.com` may result in a temporary block from Google. Use this tool responsibly for targeted outreach, not mass spam.
- **Data Privacy:** Do NOT commit your `.env` file, `sent_emails_registry.json`, or the `cvs/` folder to a public repository. Ensure your `.gitignore` is properly configured.

---

## 🤝 Contributing

Contributions, issues, and feature requests are highly welcome! Since this project was largely AI-generated, there is always room for human optimization, better error handling, and cleaner code.

**How to contribute:**
1. Fork the Project
2. Create your Feature Branch `git checkout -b feature/AmazingFeature`
3. Commit your Changes `git commit -m 'Add some AmazingFeature'`
4. Push to the Branch `git push origin feature/AmazingFeature`
5. Open a Pull Request

If you find a bug or have an idea, please open an **Issue** first to discuss it.

---

## 📄 License

Distributed under the **MIT License**. See `LICENSE` for more information.