import flask  # Import Flask library
from flask import request  # Import flask request object
import os  # Import os module to get environment variables
import boto3  # Import boto3 to interact with AWS services
from bot import ObjectDetectionBot  # Import custom bot class
import logging  # Import logging module
import json
import requests  # Import requests to make HTTP requests
import time  # Import time module for retry logic

logging.basicConfig(level=logging.INFO)  # Set logging level
logger = logging.getLogger(__name__)  # Create the logger
app = flask.Flask(__name__)  # Initialize the flask app

@app.route('/health', methods=['GET'])
def health_check():
    # You can add more checks here, like database or cache checks
    return "OK", 200

YOLOV5_URL = os.getenv("YOLOV5_URL", "http://yolo5-service.default.svc.cluster.local:5000")

def get_yolo5_results(img_name):
    try:
        response = requests.post(f"{YOLOV5_URL}/predict", json={"imgName": img_name})
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"YOLOv5 service error: {e}")
        return None

# --- Configuration ---
secrets_client = boto3.client('secretsmanager', region_name="eu-north-1")
response = secrets_client.get_secret_value(SecretId="polybot-secrets")
secrets = json.loads(response['SecretString'])

os.environ["TELEGRAM_TOKEN"] = secrets["TELEGRAM_TOKEN"]
os.environ["S3_BUCKET_NAME"] = secrets["S3_BUCKET_NAME"]
os.environ["SQS_QUEUE_URL"] = secrets["SQS_QUEUE_URL"]
os.environ["TELEGRAM_APP_URL"] = secrets["TELEGRAM_APP_URL"]
os.environ["MONGO_URI"] = secrets["MONGO_URI"]
os.environ["MONGO_DB"] = "config"
os.environ["MONGO_COLLECTION"] = "image_collection"

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
S3_BUCKET_NAME = os.environ["S3_BUCKET_NAME"]
SQS_QUEUE_URL = os.environ["SQS_QUEUE_URL"]
TELEGRAM_APP_URL = os.environ["TELEGRAM_APP_URL"]

# --- S3 Client Initialization ---
s3_client = boto3.client('s3')  # Create S3 client

# --- Bot Initialization ---
bot = ObjectDetectionBot(TELEGRAM_TOKEN, TELEGRAM_APP_URL, S3_BUCKET_NAME)  # create bot instance

# --- Define Routes ---
@app.route('/', methods=['GET'])  # Define index page
def index():
    return 'Ok'

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=30619, ssl_context=("polybot.crt", "privkey.pem"))
