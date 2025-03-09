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

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
S3_BUCKET_NAME = os.environ["S3_BUCKET_NAME"]
SQS_QUEUE_URL = os.environ["SQS_QUEUE_URL"]
TELEGRAM_APP_URL = os.environ["TELEGRAM_APP_URL"]

# --- S3 Client Initialization ---
s3_client = boto3.client('s3')  # Create S3 client

# --- Bot Initialization ---
bot = ObjectDetectionBot(TELEGRAM_TOKEN, TELEGRAM_APP_URL, S3_BUCKET_NAME)  # create bot instance

# Function to delete the existing webhook
def delete_webhook():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook"
    response = requests.get(url)
    if response.status_code == 200:
        logger.info("Existing webhook deleted successfully.")
    else:
        logger.error(f"Failed to delete webhook. Response: {response.text}")

def check_webhook_status():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getWebhookInfo"
    response = requests.get(url)

    if response.status_code == 200:
        webhook_info = response.json()
        if webhook_info['result']['url']:
            logger.info(f"Webhook is already set to: {webhook_info['result']['url']}")
            return True
        else:
            logger.info("No webhook is set.")
            return False
    else:
        logger.error(f"Failed to get webhook info. Response: {response.text}")
        return False


# Function to set the new webhook URL
def set_webhook():
    delete_webhook()  # Delete the existing webhook first
    webhook_url = f"{TELEGRAM_APP_URL}/{TELEGRAM_TOKEN}"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={webhook_url}"

    retry_count = 0
    max_retries = 5

    while retry_count < max_retries:
        response = requests.get(url)

        if response.status_code == 200:
            logger.info(f"Webhook set successfully: {webhook_url}")
            break
        elif response.status_code == 429:
            retry_after = response.json().get('parameters', {}).get('retry_after', 1)
            logger.info(f"Too many requests. Retrying after {retry_after} seconds...")
            time.sleep(retry_after)
            retry_count += 1
        else:
            logger.error(f"Failed to set webhook. Response: {response.text}")
            break

# --- Define Routes ---
@app.route('/', methods=['GET'])  # Define index page
def index():  # index function, will return "ok" for a healthy service
    return 'Ok'

# Delete existing webhook and set a new one at app startup
logger.info(f"Deleting existing webhook (if any) and setting new webhook URL.")

if not check_webhook_status():
    set_webhook()  # Set the new webhook after deletion

# Now you can define the route that handles the webhook requests
@app.route(f'/{TELEGRAM_TOKEN}/', methods=['POST'])  # Define Telegram Webhook endpoint using the bot token
def webhook():  # Create method to handle webhook requests
    try:
        req = request.get_json()  # Get the request body as JSON
        logger.info(f'Received webhook request: {req}')  # Log the received request.
        logger.info(f"Request headers: {request.headers}")
        bot.handle_message(req['message'])  # Send message to bot handle_message
        logger.info(f"Received message: {req['message']}")
        return 'Ok'  # Return 'Ok' to telegram
    except Exception as e:
        logger.error(f"Error handling webhook request: {e}")
        return 'Error', 500


# --- Main Execution ---
if __name__ == "__main__":  # Check if script is run directly.
    app.run(host="0.0.0.0", port=30619, ssl_context=("polybot.crt", "privkey.pem"))
