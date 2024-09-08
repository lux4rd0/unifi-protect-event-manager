import os
import subprocess
from datetime import datetime, timedelta
import logging
import pytz
from flask import Flask, request, jsonify, render_template
from threading import Timer, Lock, Thread
import sys
import time

# Initialize Flask app
app = Flask(__name__)

# Configure logging to output to both file and console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)


class UnifiProtectEventManager:
    def __init__(self):
        self.LOCAL_TIMEZONE = pytz.timezone(os.getenv("TZ", "UTC"))
        self.DEFAULT_PAST_MINUTES = int(os.getenv("UPEM_DEFAULT_PAST_MINUTES", 5))
        self.DEFAULT_FUTURE_MINUTES = int(os.getenv("UPEM_DEFAULT_FUTURE_MINUTES", 5))
        self.UNIFI_PROTECT_ADDRESS = os.getenv("UPEM_UNIFI_PROTECT_ADDRESS")
        self.UNIFI_PROTECT_USERNAME = os.getenv("UPEM_UNIFI_PROTECT_USERNAME")
        self.UNIFI_PROTECT_PASSWORD = os.getenv("UPEM_UNIFI_PROTECT_PASSWORD")
        self.LOG_INTERVAL = int(os.getenv("UPEM_LOG_INTERVAL", 10))
        self.MAX_RETRIES = int(os.getenv("UPEM_MAX_RETRIES", 3))
        self.RETRY_DELAY = int(os.getenv("UPEM_RETRY_DELAY", 5))

        self.events = {}
        self.timers = {}  # To track and cancel timers
        self.event_lock = Lock()

        self.check_env_variables()

        # Start the background logger
        Thread(target=self.log_active_events_periodically, daemon=True).start()

    def check_env_variables(self):
        """Check and log the required environment variables, exit if any are missing."""
        missing_vars = []
        logging.info(f"UPEM_UNIFI_PROTECT_ADDRESS: {self.UNIFI_PROTECT_ADDRESS}")
        logging.info(f"UPEM_UNIFI_PROTECT_USERNAME: {self.UNIFI_PROTECT_USERNAME}")
        logging.info(
            f"UPEM_UNIFI_PROTECT_PASSWORD: {'***' if self.UNIFI_PROTECT_PASSWORD else 'Not Set'}"
        )

        if not self.UNIFI_PROTECT_ADDRESS:
            missing_vars.append("UPEM_UNIFI_PROTECT_ADDRESS")
        if not self.UNIFI_PROTECT_USERNAME:
            missing_vars.append("UPEM_UNIFI_PROTECT_USERNAME")
        if not self.UNIFI_PROTECT_PASSWORD:
            missing_vars.append("UPEM_UNIFI_PROTECT_PASSWORD")

        if missing_vars:
            logging.error(f"Missing environment variables: {', '.join(missing_vars)}")
            sys.exit(1)

        logging.info(
            "All required environment variables are set. Proceeding with startup..."
        )

    def current_time(self):
        return datetime.now(self.LOCAL_TIMEZONE)

    def format_datetime(self, dt):
        """Ensure datetime is localized to the correct timezone and return a formatted string."""
        if dt.tzinfo is None:  # If the datetime object is naive (not timezone-aware)
            dt = self.LOCAL_TIMEZONE.localize(dt)
        return dt.strftime("%Y-%m-%d %H:%M:%S%z")

    def extend_event(
        self, identifier, past_minutes=None, future_minutes=None, cameras=None
    ):
        current_time = self.current_time()

        # Use the provided values or fall back to the defaults
        new_past_minutes = (
            past_minutes if past_minutes is not None else self.DEFAULT_PAST_MINUTES
        )
        new_future_minutes = (
            future_minutes
            if future_minutes is not None
            else self.DEFAULT_FUTURE_MINUTES
        )

        with self.event_lock:
            if identifier in self.events:
                # Cancel the previous timer
                if identifier in self.timers:
                    self.timers[identifier].cancel()

                # Extend the event
                event = self.events[identifier]
                event["end_time"] = current_time + timedelta(minutes=new_future_minutes)
                event["cameras"] = cameras
                message = f"Event {identifier} extended"
            else:
                # Create a new event
                start_time = current_time - timedelta(minutes=new_past_minutes)
                end_time = current_time + timedelta(minutes=new_future_minutes)
                self.events[identifier] = {
                    "start_time": start_time,
                    "end_time": end_time,
                    "cameras": cameras,
                }
                message = f"New event {identifier} started"

            logging.info(
                f"{message}. Start: {self.events[identifier]['start_time']}, End: {self.events[identifier]['end_time']}"
            )

            # Schedule the export at the event end time
            delay = (self.events[identifier]["end_time"] - current_time).total_seconds()
            logging.info(
                f"Scheduling export for event {identifier} in {delay:.6f} seconds."
            )
            timer = Timer(delay, self.execute_export, args=[identifier])
            timer.start()
            self.timers[identifier] = timer  # Store the timer for cancellation

            return message

    def cancel_event(self, identifier):
        with self.event_lock:
            if identifier in self.events:
                # Cancel the timer if it exists
                if identifier in self.timers:
                    self.timers[identifier].cancel()
                    del self.timers[identifier]

                del self.events[identifier]
                logging.info(f"Cancelled event {identifier}.")
            else:
                logging.warning(f"No event found with identifier {identifier}.")

    def status_event(self, identifier=None):
        with self.event_lock:
            if identifier:
                event = self.events.get(identifier)
                if event:
                    remaining_time = (
                        event["end_time"] - self.current_time()
                    ).total_seconds()
                    if remaining_time > 0:
                        return {
                            "events": {
                                identifier: {
                                    "start_time": self.format_datetime(
                                        event["start_time"]
                                    ),
                                    "end_time": self.format_datetime(event["end_time"]),
                                    "remaining_time_seconds": remaining_time,
                                    "cameras": event["cameras"],
                                }
                            }
                        }
                    else:
                        self.cancel_event(identifier)
                        return {"events": {identifier: {"status": "no_event"}}}
                else:
                    return {"events": {identifier: {"status": "no_event"}}}
            else:
                all_events = {}
                for id, event in self.events.items():
                    remaining_time = (
                        event["end_time"] - self.current_time()
                    ).total_seconds()
                    if remaining_time > 0:
                        all_events[id] = {
                            "start_time": self.format_datetime(event["start_time"]),
                            "end_time": self.format_datetime(event["end_time"]),
                            "remaining_time_seconds": remaining_time,
                            "cameras": event["cameras"],
                        }
                    else:
                        self.cancel_event(id)
                        all_events[id] = {"status": "no_event"}
                return {"events": all_events}

    def execute_export(self, identifier):
        # Check if the event still exists (may have been cancelled)
        with self.event_lock:
            event = self.events.get(identifier)
            if not event:
                logging.info(
                    f"Event {identifier} was already cancelled or does not exist."
                )
                return

        logging.info(f"Running protect-archiver command for event {identifier}.")
        start_str = event["start_time"].strftime("%Y-%m-%d %H:%M:%S%z")
        end_str = event["end_time"].strftime("%Y-%m-%d %H:%M:%S%z")

        cameras_arg = (
            "--cameras=all"
            if not event["cameras"] or all(camera == "" for camera in event["cameras"])
            else f"--cameras={','.join(event['cameras'])}"
        )
        logging.info(f"Using cameras: {cameras_arg}")

        script_dir = os.path.dirname(os.path.realpath(__file__))
        downloads_folder = os.path.join(script_dir, "downloads")
        folder_time = event["start_time"].strftime("%Y/%m/%d/%H.%M.%S")
        target_folder = os.path.join(downloads_folder, folder_time)

        if not os.path.exists(target_folder):
            os.makedirs(target_folder)
            logging.info(f"Created target folder: {target_folder}")
        else:
            logging.info(f"Target folder {target_folder} already exists.")

        command = [
            "protect-archiver",
            "download",
            "--address",
            self.UNIFI_PROTECT_ADDRESS,
            "--username",
            self.UNIFI_PROTECT_USERNAME,
            "--password",
            self.UNIFI_PROTECT_PASSWORD,
            "--start",
            start_str,
            "--end",
            end_str,
            cameras_arg,
            "--no-use-subfolders",
            target_folder,
        ]

        logging.info(f"Executing command: {' '.join(command)}")

        attempt = 0
        while attempt < self.MAX_RETRIES:
            try:
                process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )

                for stdout_line in iter(process.stdout.readline, ""):
                    logging.info(stdout_line.strip())

                for stderr_line in iter(process.stderr.readline, ""):
                    logging.error(stderr_line.strip())

                process.stdout.close()
                process.stderr.close()
                process.wait()

                if process.returncode == 0:
                    logging.info(f"Export completed for event {identifier}.")
                    break  # Exit loop on successful execution
                else:
                    logging.error(
                        f"Export failed for event {identifier} with return code {process.returncode}. Retrying..."
                    )

            except subprocess.CalledProcessError as e:
                logging.error(f"Export failed for event {identifier}: {e}. Retrying...")

            # Increment attempt counter and delay before retrying
            attempt += 1
            if attempt < self.MAX_RETRIES:
                logging.info(
                    f"Retrying in {self.RETRY_DELAY} seconds... (Attempt {attempt}/{self.MAX_RETRIES})"
                )
                time.sleep(self.RETRY_DELAY)
            else:
                logging.error(
                    f"Max retries reached. Failed to export event {identifier}."
                )

        # Safely delete the event after execution
        with self.event_lock:
            if identifier in self.events:
                del self.events[identifier]
                logging.info(f"Event {identifier} successfully removed after export.")
            else:
                logging.warning(f"Event {identifier} was already removed.")

    def log_active_events_periodically(self):
        """Logs the status of active events every configured interval."""
        while True:
            with self.event_lock:
                if self.events:
                    logging.info("Logging active events:")
                    for id, event in self.events.items():
                        remaining_time = (
                            event["end_time"] - self.current_time()
                        ).total_seconds()
                        if remaining_time > 0:
                            logging.info(
                                f"Event {id} | Start: {self.format_datetime(event['start_time'])}, "
                                f"End: {self.format_datetime(event['end_time'])}, "
                                f"Remaining: {remaining_time:.2f} seconds, Cameras: {event['cameras']}"
                            )
                        else:
                            logging.info(f"Event {id} has ended.")
            time.sleep(self.LOG_INTERVAL)


# Flask Routes
manager = UnifiProtectEventManager()


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/start", methods=["POST"])
def start_event():
    try:
        data = request.get_json()
        identifier = data.get("identifier")
        past_minutes = data.get("past_minutes")
        future_minutes = data.get("future_minutes")
        cameras = data.get("cameras", [])

        if not identifier:
            raise ValueError("Missing event identifier")

        message = manager.extend_event(
            identifier, past_minutes, future_minutes, cameras
        )

        status = manager.status_event(identifier)
        status["message"] = message
        return jsonify(status)
    except Exception as e:
        logging.error(f"Error starting event: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/cancel", methods=["POST"])
def cancel_event():
    try:
        data = request.get_json()
        identifier = data.get("identifier")
        if not identifier:
            raise ValueError("Missing event identifier")

        manager.cancel_event(identifier)
        return jsonify({"status": "cancelled"})
    except Exception as e:
        logging.error(f"Error cancelling event: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/status", methods=["GET"])
def status_event():
    try:
        identifier = request.args.get("identifier")
        status = manager.status_event(identifier)
        return jsonify(status)
    except Exception as e:
        logging.error(f"Error getting status: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8888, debug=True)
