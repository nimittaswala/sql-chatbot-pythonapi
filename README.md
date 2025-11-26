SQL Chatbot – Python FastAPI Backend

This is the backend API for the SQL Chatbot.
It receives user questions, selects tables, and generates SQL queries and explanations.

⭐ Getting Started
1. Install dependencies
   pip install -r requirements.txt

2. Run the server
   uvicorn main:app --reload --host 0.0.0.0 --port 9001

The API will run at:
  http://localhost:9001

📁 Main Endpoints
Generate SQL (Basic)
 POST /generate_sql

 Generate SQL (Premium / Cluster)
   POST /generate_sql_premium


🌐 CORS Setup

Make sure your frontend URL is allowed:
origins = [
    "http://localhost:5173",
    "https://chat.local",
]

🔧 Configuration Notes

Update your OpenAI API key in the .env file:
OPENAI_API_KEY=your_key_here

DB_HOST=
DB_USER=
DB_PASS=
DB_NAME=

📦 Project Structure
/main.py        → FastAPI entry point
/sql/           → SQL generation logic
/models/        → Pydantic request/response models
/utils/         → Helpers and table-selection code


🧪 Testing the API

Open in browser or Postman:
http://localhost:9001/generate_sql
http://localhost:9001/generate_sql_premium

📝 Notes

If using https://chat.local, add it to hosts file:
127.0.0.1  chat.local

Use a reverse proxy (IIS / Nginx) if you want HTTPS.
Make sure the frontend and backend URLs match.





