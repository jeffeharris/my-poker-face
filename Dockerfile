FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Create directory for database
RUN mkdir -p /app/data

# Make scripts executable
RUN chmod +x bin/docker-entrypoint.sh bin/seed_personalities.py

# Expose the Flask port
EXPOSE 5000

# Set environment variables
ENV FLASK_APP=flask_app.ui_web
ENV PYTHONPATH=/app

# Use entrypoint to run setup tasks before starting app
ENTRYPOINT ["bin/docker-entrypoint.sh"]

# Run the Flask app
CMD ["python", "-m", "flask", "run", "--host=0.0.0.0", "--port=5000"]