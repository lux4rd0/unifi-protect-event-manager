# Use the unifitoolbox/protect-archiver as the base image
FROM unifitoolbox/protect-archiver:latest

# Install Python and pip
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    && apt-get clean

# Set the working directory for your app
WORKDIR /app

# Copy the requirements file
COPY requirements.txt ./

# Install Python dependencies
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy all files for your Flask app
COPY . .

# Expose the port your Flask app runs on
EXPOSE 8888

# Override the default entrypoint of the base image
ENTRYPOINT ["python", "./unifi_protect_event_manager.py"]
