# Hospital Appointment System

## Run locally (Windows / Mac / Linux)
    python -m venv venv
    venv\Scripts\activate          # Mac/Linux: source venv/bin/activate
    python -m pip install -r requirements.txt
    python app.py                  # open http://localhost:5000

Register a doctor first (with a specialization), then a patient in a private window, and book a slot.

<img width="1908" height="901" alt="Screenshot 2026-10-25 222048" src="https://github.com/user-attachments/assets/a6d7abe5-d778-47ee-a9da-a7748721831e" />

## Features
- Patient and doctor accounts (JWT login, hashed passwords)
- Search doctors by name or specialization
- 30-minute slots generated from each doctor's working hours; past and taken slots are disabled
- Double booking is blocked in the database itself (unique index)
- Patients and doctors can cancel; doctors mark visits completed and add notes

## Deploy to the cloud
- Set SECRET_KEY, and optionally DB_PATH
- Linux: pip install gunicorn, run `gunicorn app:app`. Windows: pip install waitress, run `waitress-serve app:app`
- Move to PostgreSQL (AWS RDS) for production and use HTTPS
- Hosting options: AWS Elastic Beanstalk, EC2 + Nginx, Render, Railway
