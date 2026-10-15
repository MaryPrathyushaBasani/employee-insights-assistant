# Employee Insights Assistant

A Flask chat application for workforce questions, grounded business analysis, result-based charts, and downloadable PDF reports. It supports text and browser voice input, reads voice questions aloud, and keeps conversation history in the browser. The interface and PDF reports use Times New Roman.

## Run locally

1. From the repository root, enter the app folder with `cd mary_chatbot`.
2. Create and activate a Python virtual environment (`python -m venv .venv`).
3. Install dependencies with `python -m pip install -r requirements.txt`.
4. Copy `.env.example` to `.env` and add your local service credentials.
5. Start the app with `python app.py` and open `http://127.0.0.1:5005/` (the default; set `PORT` to use another port).

On Windows, activate the environment with `.venv\Scripts\activate`. On macOS or Linux, use `source .venv/bin/activate`.

```dotenv
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=your_mysql_user
MYSQL_PASSWORD=your_mysql_password
MYSQL_SCHEMA=your_mysql_schema
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_API_KEY=your_api_key
AZURE_OPENAI_API_VERSION=your_api_version
AZURE_OPENAI_DEPLOYMENT_MINI=your_deployment_name
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your_smtp_user
SMTP_PASSWORD=your_smtp_password
SMTP_FROM_EMAIL=reports@example.com
SMTP_USE_TLS=true
SMTP_USE_SSL=false
```

Keep `.env` private. It is excluded from Git. The sample SMTP host is Gmail; replace it and the sender settings with values for your provider. Gmail requires an app password when using SMTP. SMTP settings are required only for sending reports; PDF downloads work without them. Open **Report Settings** in the header to set a recipient, send the latest report, or turn on automatic report email. The interface confirms submission only after the SMTP server accepts the message.

Ask for bar, column, line, area, pie, scatter, histogram, box, or heatmap charts, or let the assistant choose a chart from the available result shape. When the requested chart does not fit the returned data, the assistant explains why and offers an alternative when possible. Charts are shown in chat and included in the PDF with labels and units.

Reports include an executive summary, objective and scope, data source and cleaning methodology, verified findings, possible explanations clearly marked as unverified, chart figures, implications, recommendations, limitations, and a conclusion. Query results are cleaned in memory; the original database data is never updated.

Voice input depends on browser speech recognition support and microphone permission. Voice output uses the browser's speech synthesis support.