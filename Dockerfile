# Use Python 3.12 as base image
FROM python:3.12

# Set environment variables
ENV PYTHONUNBUFFERED=1
WORKDIR /

# Copy project files
COPY . /

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Export PYTHONPATH
ENV PYTHONPATH="${PYTHONPATH}:${pwd}"

# Expose ports for both applications
EXPOSE 5000

# Run both FastAPI and Flask applications simultaneously
CMD bash -c "export PYTHONPATH=\$PYTHONPATH:\$(pwd) && \
    python apps/fastapi/app.py & \
    wait"
