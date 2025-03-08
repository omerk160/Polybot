import flask  # Import Flask library
from flask import request  # Import flask request object
import os  # Import os module to get environment variables
import boto3  # Import boto3 to interact with AWS services
from bot import ObjectDetectionBot  # Import custom bot class
import logging  # Import logging module
import json
import requests  # Import requests to make HTTP requests

logging.basicConfig(level=logging.INFO)  # Set logging level
logger = logging.getLogger(__name__)  # Create the logger
app = flask.Flask(__name__)  # Initialize the flask app

@app.route('/health', methods=['GET'])
def health_check():
    # You can add more checks here, like database or cache checks
    return "OK", 200

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

# Function to set the new webhook URL
def set_webhook():
    delete_webhook()  # Delete the existing webhook first
    webhook_url = f"{TELEGRAM_APP_URL}/{TELEGRAM_TOKEN}"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={webhook_url}"
    response = requests.get(url)
    if response.status_code == 200:
        logger.info(f"Webhook set successfully: {webhook_url}")
    else:
        logger.error(f"Failed to set webhook. Response: {response.text}")

# --- Define Routes ---
@app.route('/', methods=['GET'])  # Define index page
def index():  # index function, will return "ok" for a healthy service
    return 'Ok'

# Delete existing webhook and set a new one at app startup
logger.info(f"Deleting existing webhook (if any) and setting new webhook URL.")

set_webhook()  # Set the new webhook after deletion

# Now you can define the route that handles the webhook requests
@app.route(f'/{TELEGRAM_TOKEN}/', methods=['POST'])  # Define Telegram Webhook endpoint using the bot token
def webhook():  # Create method to handle webhook requests
    req = request.get_json()  # Get the request body as JSON
    logger.info(f'Received webhook request: {req}')  # Log the received request.
    bot.handle_message(req['message'])  # Send message to bot handle_message
    logger.info(f"Received message: {req['message']}")
    return 'Ok'  # Return 'Ok' to telegram

# --- Main Execution ---
if __name__ == "__main__":  # Check if script is run directly.
    app.run(host="0.0.0.0", port=30619, ssl_context=("polybot.crt", "privkey.pem"))
