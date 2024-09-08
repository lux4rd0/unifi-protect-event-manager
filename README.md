# Unifi Protect Event Manager

The **Unifi Protect Event Manager** is a Flask-based service that integrates with UniFi Protect to manage and trigger video exports based on events like motion detection or doorbell rings. It allows for controlling video recording events by specifying past and future times and filtering cameras. 

## Features

- **Event Management**: Start, extend, or cancel video recording events.
- **Multiple Camera Support**: Trigger events for one or multiple cameras.
- **Flexible Timing**: Configure how far back and how far into the future to record.
- **Status Logging**: This function periodically logs the status of active events, providing the remaining time, start/end times, and involved cameras.
- **Home Assistant Integration**: Easily trigger events from Home Assistant automation.
- **Dockerized Deployment**: Easily deploy via Docker Compose.

---

## Table of Contents
1. [How it Works](#how-it-works)
2. [Deployment](#deployment)
    - [Docker Compose](#docker-compose)
    - [Environment Variables](#environment-variables)
3. [Home Assistant Integration](#home-assistant-integration)
    - [REST Command](#rest-command)
    - [Automation Example](#automation-example)
4. [API Endpoints](#api-endpoints)

---

## How it Works

The service runs as a Flask web app, exposing several API endpoints to manage events. Events represent time periods during which video footage from one or more UniFi cameras is exported. These events can be started, extended, or canceled.

Each event has a start time, an end time, a list of cameras, and an associated identifier. Events can be managed via the following operations:
- **Start/Extend Event**: Create or extend an event by specifying how many minutes in the past and future to record.
- **Cancel Event**: Cancel a running event before it completes.
- **View Status**: View the status of all running events, including remaining time, start/end time, and cameras.

The system is designed to periodically log the current status of active events and automatically delete them once they are complete.

### Video Export using Protect-Archiver

This project relies on [**unifitoolbox/protect-archiver**](https://github.com/unifi-toolbox/protect-archiver) to manage the actual video export. **Protect-archiver** is a command-line utility that interfaces with UniFi Protect to download, list, or delete recordings.

The integration with **protect-archiver** allows this service to trigger video exports at specific times by executing protect-archiver commands in the background. Here’s how it works:

- When an event is triggered via the Flask API, the system calculates the start and end time for video export.
- It dynamically constructs and runs the **protect-archiver** command to download footage within the defined time range.
- The cameras to be included in the export can be specified or defaulted to all cameras.
  
Example command executed by the system:
```
protect-archiver download \
  --address <UNIFI_PROTECT_ADDRESS> \
  --username <UNIFI_PROTECT_USERNAME> \
  --password <UNIFI_PROTECT_PASSWORD> \
  --start <start_time> \
  --end <end_time> \
  --cameras=<cameras> \
  --no-use-subfolders <output_folder>
```

The full details of **protect-archiver** can be found in the official GitHub repository: [unifitoolbox/protect-archiver](https://github.com/unifi-toolbox/protect-archiver).

This project utilizes **protect-archiver** to simplify the interaction with UniFi Protect and automate the recording export process based on specific events. This makes it an ideal tool for managing video exports with precise control over timing and camera selection.

---

## Deployment

### Docker Compose

You can use the following Docker Compose setup to deploy this service via Docker.

1. Create a `docker-compose.yml` file with the following contents:

```yaml
version: "3"
services:
  unifi_protect_event_manager:
    image: your-docker-image-name
    container_name: unifi_protect_event_manager
    ports:
      - "8888:8888"
    environment:
      TZ: "America/Chicago"
      UNIFI_PROTECT_ADDRESS: "your-unifi-protect-address"
      UNIFI_PROTECT_USERNAME: "your-username"
      UNIFI_PROTECT_PASSWORD: "your-password"
      DEFAULT_PAST_MINUTES: 5
      DEFAULT_FUTURE_MINUTES: 5
      LOG_INTERVAL: 10
    volumes:
      - ./downloads:/app/downloads
    restart: unless-stopped
```

2. Run `docker-compose up -d` to start the service.

### Environment Variables

- **`TZ`**: Timezone setting (defaults to UTC if not set).
- **`UNIFI_PROTECT_ADDRESS`**: Address of your UniFi Protect instance.
- **`UNIFI_PROTECT_USERNAME`**: Username for accessing UniFi Protect.
- **`UNIFI_PROTECT_PASSWORD`**: Password for accessing UniFi Protect.
- **`DEFAULT_PAST_MINUTES`**: Default time in minutes to record from the past.
- **`DEFAULT_FUTURE_MINUTES`**: Default time in minutes to record into the future.
- **`LOG_INTERVAL`**: Interval in seconds when the system logs active event status.

---

## Home Assistant Integration

This system can be easily integrated into Home Assistant to trigger video exports based on motion or other triggers.

### REST Command

In Home Assistant, set up a REST command to trigger events in the UniFi Protect Event Manager.

1. Add the following to your `configuration.yaml`:

```yaml
rest_command:
  start_unifi_protect_event:
    url: "http://nas02.boutique.brieflybeautiful.com:8888/start"
    method: POST
    headers:
      content-type: "application/json"
    payload: >
      {
        "identifier": "{{ identifier }}",
        "past_minutes": {{ past_minutes }},
        "future_minutes": {{ future_minutes }},
        "cameras": [ {% for camera in cameras %} "{{ camera }}"{% if not loop.last %}, {% endif %}{% endfor %} ]
      }
```

### Automation Example

You can trigger the REST command in an automation based on motion or other sensors:

```yaml
alias: Trigger Video Export on Motion
trigger:
  - platform: state
    entity_id: binary_sensor.front_door_motion
    to: 'on'
action:
  - service: rest_command.start_unifi_protect_event
    data:
      identifier: "front_door_motion"
      past_minutes: 5
      future_minutes: 5
      cameras:
        - 65eb91b702d34c03e40316c2
```

This automation starts an event to export video footage from the past 5 minutes and future 5 minutes when motion is detected.

---

## API Endpoints

Here are the key API endpoints available in the UniFi Protect Event Manager:

- **Start Event**
  - **Endpoint**: `/start`
  - **Method**: `POST`
  - **Payload Example**:
    ```json
    {
      "identifier": "door1",
      "past_minutes": 5,
      "future_minutes": 10,
      "cameras": ["camera_id_1", "camera_id_2"]
    }
    ```
  
- **Cancel Event**
  - **Endpoint**: `/cancel`
  - **Method**: `POST`
  - **Payload Example**:
    ```json
    {
      "identifier": "door1"
    }
    ```

- **Event Status**
  - **Endpoint**: `/status`
  - **Method**: `GET`
  - **Query Parameter**: `identifier=<event_identifier>`
  - **Example**: `/status?identifier=door1`

