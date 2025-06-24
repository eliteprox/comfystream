# ComfyStream BYOC (Bring Your Own Container) Dockerfile
# Based on the BYOC documentation for Livepeer integration

FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install additional dependencies for BYOC server
RUN pip install --no-cache-dir \
    aiohttp \
    aiohttp-cors \
    asyncio

# Copy the application code
COPY . .

# Install ComfyStream in development mode
RUN pip install -e .

# Create directories for ComfyUI workspace
RUN mkdir -p /workspace

# Expose the BYOC server port
EXPOSE 5000

# Environment variables
ENV PYTHONPATH=/app
ENV WORKSPACE=/workspace

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# Default command to run the BYOC server
CMD ["python", "example_byoc_server.py", "--workspace", "/workspace", "--host", "0.0.0.0", "--port", "5000"]
