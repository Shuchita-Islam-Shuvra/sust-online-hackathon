FROM python:3.11-slim

WORKDIR /app

# Install system dependencies if any are needed (none required for pure python, but we keep it light)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ app/

# Expose port (default 8000, can be overridden)
EXPOSE 8000

# Set environment variables default
ENV PORT=8000
ENV PYTHONUNBUFFERED=1

# Command to run the application
CMD ["python", "-m", "app.main"]
