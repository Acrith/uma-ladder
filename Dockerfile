# Use slim Python image
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies (for SQLite + PIL if used)
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app source
COPY . .

# Expose the port Fly expects
EXPOSE 8080

# Run app with Gunicorn
CMD ["gunicorn", "-b", "0.0.0.0:8080", "app:app"]
