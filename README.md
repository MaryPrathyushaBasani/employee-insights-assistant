# Employee Insights Assistant

A Flask chat application for asking workforce questions in plain English. It supports text and browser voice input, reads voice questions aloud, and keeps conversation history in the browser.

## Run locally

1. Create and activate a Python virtual environment.
2. Install the dependencies with `python -m pip install -r requirements.txt`.
3. Create a local `.env` file with the configuration keys below.
4. Start the app with `python app.py` and open `http://127.0.0.1:5001/`.

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
```

Keep `.env` private. It is excluded from Git. The database must contain the employee data expected by the application.

Voice input depends on browser speech recognition support and microphone permission. Voice output uses the browser's speech synthesis support.