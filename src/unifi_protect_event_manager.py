import os
import subprocess
from datetime import datetime, timedelta
import logging
import pytz
from flask import Flask, request, jsonify, render_template
from threading import Timer, Lock, Thread
import sys
import time
import re

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
        self.EXPORT_TIMEOUT = int(os.getenv("UPEM_EXPORT_TIMEOUT", 300))
        self.KEEP_SPLIT_FILES = (
            os.getenv("UPEM_KEEP_SPLIT_FILES", "true").lower() == "true"
        )

        self.events = {}
        self.timers = {}
        self.event_lock = Lock()

        self.check_env_variables()

        Thread(target=self.log_active_events_periodically, daemon=True).start()

    def check_env_variables(self):
        """Check and log the required environment variables, exit if any are missing."""
        missing_vars = []
        logging.info(f"UPEM_UNIFI_PROTECT_ADDRESS: {self.UNIFI_PROTECT_ADDRESS}")
        logging.info(f"UPEM_UNIFI_PROTECT_USERNAME: {self.UNIFI_PROTECT_USERNAME}")
        logging.info(
            f"UPEM_UNIFI_PROTECT_PASSWORD: {'***' if self.UNIFI_PROTECT_PASSWORD else 'Not Set'}"
        )
        logging.info(f"UPEM_KEEP_SPLIT_FILES: {self.KEEP_SPLIT_FILES}")

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
                f"{message}. Start: {self.format_datetime(self.events[identifier]['start_time'])}, "
                f"End: {self.format_datetime(self.events[identifier]['end_time'])}"
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

    def combine_videos(self, folder_path):
        """
        This method will group and combine videos in the specified folder based on their camera name and order them by timestamp.
        It will remove the original files if UPEM_KEEP_SPLIT_FILES is set to 'false'.
        """
        video_files = [f for f in os.listdir(folder_path) if f.endswith(".mp4")]

        if not video_files:
            logging.info(f"No .mp4 files found in {folder_path}.")
            return

        logging.info(f"Found {len(video_files)} video files in {folder_path}.")

        def get_camera_name(filename):
            # Extract everything before the timestamp part (i.e., the camera name)
            match = re.match(
                r"(.+?) - \d{4}-\d{2}-\d{2} - \d{2}\.\d{2}\.\d{2}-\d{4}\.mp4", filename
            )
            if match:
                return match.group(1)
            return None

        def extract_timestamp(filename):
            # Extract timestamp from filename, assuming the format "YYYY-MM-DD - HH.MM.SS-xxxx"
            match = re.search(r"(\d{4}-\d{2}-\d{2}) - (\d{2}\.\d{2}\.\d{2})", filename)
            if match:
                date_part, time_part = match.groups()
                # Combine the date and time part to create a datetime object for sorting
                return datetime.strptime(
                    f"{date_part} {time_part}", "%Y-%m-%d %H.%M.%S"
                )
            return None

        grouped_videos = {}

        for video_file in video_files:
            camera_name = get_camera_name(video_file)
            if camera_name:
                logging.info(f"Grouping video: {video_file} under {camera_name}")
                if camera_name not in grouped_videos:
                    grouped_videos[camera_name] = []
                grouped_videos[camera_name].append(
                    os.path.join(folder_path, video_file)
                )

        for camera_name, video_group in grouped_videos.items():
            if len(video_group) > 1:
                # Sort videos by their extracted timestamps
                video_group.sort(key=lambda v: extract_timestamp(os.path.basename(v)))

                # Use the name of the first video for the output filename
                first_video_name = os.path.basename(video_group[0])
                output_filename = first_video_name.replace(".mp4", " - combined.mp4")
                output_filepath = os.path.join(folder_path, output_filename)

                logging.info(
                    f"Combining {len(video_group)} videos into {output_filepath}"
                )

                # Create a temporary filelist for ffmpeg
                with open(os.path.join(folder_path, "filelist.txt"), "w") as f:
                    for video in video_group:
                        f.write(f"file '{video}'\n")

                # Run ffmpeg to concatenate videos
                concat_command = [
                    "ffmpeg",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    os.path.join(folder_path, "filelist.txt"),
                    "-c",
                    "copy",
                    output_filepath,
                ]
                subprocess.run(concat_command)

                logging.info(
                    f"Combined {len(video_group)} videos into {output_filepath}"
                )

                # Cleanup - Remove original files if KEEP_SPLIT_FILES is false
                if not self.KEEP_SPLIT_FILES:
                    for video in video_group:
                        logging.info(f"Removing original video: {video}")
                        os.remove(video)

                # Remove the temporary filelist
                os.remove(os.path.join(folder_path, "filelist.txt"))
            else:
                logging.info(
                    f"Only one video found for {camera_name}, skipping combination."
                )

    def execute_export(self, identifier):
        event = None
        with self.event_lock:
            event = self.events.pop(identifier, None)
            if identifier in self.timers:
                del self.timers[identifier]

        if not event:
            logging.info(f"Event {identifier} was already cancelled or does not exist.")
            return

        logging.info(f"Starting export process for event {identifier}")

        start_str = event["start_time"].strftime("%Y-%m-%d %H:%M:%S%z")
        end_str = event["end_time"].strftime("%Y-%m-%d %H:%M:%S%z")

        cameras_arg = (
            "--cameras=all"
            if not event["cameras"] or all(camera == "" for camera in event["cameras"])
            else f"--cameras={','.join(event['cameras'])}"
        )

        script_dir = os.path.dirname(os.path.realpath(__file__))
        downloads_folder = os.path.join(script_dir, "downloads")
        folder_time = event["start_time"].strftime("%Y/%m/%d/%H.%M.%S")

        # Include identifier in the folder structure
        target_folder = os.path.join(downloads_folder, identifier, folder_time)

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

        # Capture the output and log it
        for attempt in range(self.MAX_RETRIES):
            try:
                process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )

                # Log stdout
                for stdout_line in iter(process.stdout.readline, ""):
                    logging.info(stdout_line.strip())

                # Log stderr
                for stderr_line in iter(process.stderr.readline, ""):
                    logging.info(stderr_line.strip())

                process.stdout.close()
                process.stderr.close()
                process.wait()

                if process.returncode == 0:
                    logging.info(f"Export completed for event {identifier}.")
                    break
                else:
                    logging.error(
                        f"Export failed for event {identifier} with return code {process.returncode}. Retrying..."
                    )

            except subprocess.CalledProcessError as e:
                logging.error(f"Export failed for event {identifier}: {e}. Retrying...")

            if attempt < self.MAX_RETRIES - 1:
                logging.info(
                    f"Retrying in {self.RETRY_DELAY} seconds... (Attempt {attempt + 1}/{self.MAX_RETRIES})"
                )
                time.sleep(self.RETRY_DELAY)
        else:
            logging.error(f"Max retries reached. Failed to export event {identifier}.")

        logging.info(f"Finished export process for event {identifier}")

        # Combine the videos after the export completes
        logging.info(f"Starting video combination for folder {target_folder}")
        self.combine_videos(target_folder)

    def log_active_events_periodically(self):
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
    app.run(host="0.0.0.0", port=8888, debug=False, threaded=True)
