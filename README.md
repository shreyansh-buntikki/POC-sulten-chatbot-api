# 🚀 FastAPI Backend Project

## 📌 Overview

This project is a dual-backend system consisting of **FastAPI** application. It is designed to provide RESTful APIs with efficient request handling, logging, and database management.

## 📂 Project Structure

```
📦 project_root
├── 📂 apps
│   ├── 📂 fastapi  # FastAPI backend application
├── 📂 libs
│   ├── 📂 utils
│   │   ├── 📂 common
│   │   │   ├── 📂 models
│   │   │   │   ├── 📂 fastapi
│   │   │   ├── 📂 helpers
│   │   │   └── 📂 exceptions
│   │   ├── 📂 db
│   │   │   └── 📂 schemas
│   │   ├── 📂 config
│   │   └── 📂 logger
│   ├── 📂 vendors
│   │    ├── 📂 teams
│   │    ├── 📂 aws
│   │    └── 📂 slack
│   ├── 📂 services
│       ├── 📂 fastapi
├── 📂 tests
│   ├── 📂 fastapi
├── 📄 README.md
├── 📄 requirements.txt
├── 📄 Dockerfile
├── 📄 .env
├── 📄 example.env
├── 📄 .pre-commit-config.yaml
└── 📄 ecosystem.config.js

```

## 🏗️ Technologies Used

- **FastAPI** - High-performance backend framework for asynchronous processing.
- **PM2** - Process manager for running and managing both apps.
- **Docker** - Containerized deployment of the backend services.
- **Gunicorn** - WSGI server for running Flask applications.
- **Logging** - Centralized logging for both services.
- **Pre-commit** - Ensures code quality by running automated checks before commits.
- **Database** - Supports various databases with proper ORM handling.

## 🚀 Installation & Setup

### 2️⃣ Set Up Virtual Environment

```sh
python3.12 -m venv venv
source venv/bin/activate  # For macOS/Linux
venv\Scripts\activate    # For Windows
```

### 3️⃣ Install Dependencies

```sh
pip install -r requirements.txt
```

### 4️⃣ Set Up Environment Variables

Create a `.env` file in the root directory and configure the required environment variables. Check `example.env` for the required details.

### 5️⃣ Pre-commit Setup

This project uses pre-commit hooks to maintain code quality. To install and activate pre-commit, run:

```sh
pip install pre-commit
pre-commit install
```

To manually run pre-commit hooks on all files:

```sh
pre-commit run --all-files
```

### 6⃣ Running the Applications

#### Using PM2 (Recommended)

```sh
pm install -g pm2
pm2 start ecosystem.config.js
```

#### Using Python Manually

```sh
python apps/fastapi/app.py &
```

## 🐳 Running with Docker

### Build the Docker Image

```sh
docker build -t fastapi-app .
```

### Run the Container

```sh
docker run -p 5000:5000 fastapi-app
```

## 📌 API Endpoints

### FastAPI Endpoints (Port 5000)

- `GET /fastapi/` - Check FastAPI server up
- `GET /fastapi/health-check` - Check FastAPI server health

## 🛠️ Testing

Run the tests using:

```sh
pytest tests/fastapi
```

## 📜 Logging

Logs are stored and managed using `libs/utils/logger`.

## 🔥 Troubleshooting

### Issue: Port Already in Use

```sh
sudo lsof -i :5000
sudo kill -9 <PID>
```

### Issue: Docker Daemon Not Running

```sh
sudo systemctl start docker
```
