# Use an official Python slim runtime as a parent image
FROM python:3.11-slim-bookworm

# Set metadata and environment variables
LABEL maintainer="Nexora AI <engineering@nexora.ai>"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FLASK_APP=web_app.py

# Set the working directory in the container
WORKDIR /app

# Create unprivileged user for execution sandbox constraints
RUN groupadd -r nexora && useradd -r -g nexora nexora

# Install system dependencies (e.g., git for repository imports)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
# Assuming requirements.txt exists; we'll copy it first to leverage Docker cache
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy the core platform code
COPY . /app/

# Establish data and workspace directories with correct ownership
RUN mkdir -p /app/data /app/workspaces && \
    chown -R nexora:nexora /app/data /app/workspaces

# Switch to the unprivileged user
USER nexora

# Expose the API port
EXPOSE 5000

# Define the production entrypoint using a WSGI server (Gunicorn)
# Note: Using threads enables the LightweightWorker daemon threads to operate
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "8", "--timeout", "120", "web_app:app"]
