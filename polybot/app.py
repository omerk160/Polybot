import flask
from flask import request
import os
import boto3
from bot import ObjectDetectionBot
import logging
import json
import requests
import time
import pymongo

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = flask.Flask(__name__)

# --- Fetch secrets from AWS Secrets Manager ---
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
s3_client = boto3.client('s3')

# --- Bot Initialization ---
bot = ObjectDetectionBot(TELEGRAM_TOKEN, S3_BUCKET_NAME)

def check_webhook_status():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getWebhookInfo"
    response = requests.get(url)
    if response.status_code == 200:
        webhook_info = response.json()
        if webhook_info['result']['url']:
            logger.info(f"Webhook is already set to: {webhook_info['result']['url']}")
            return True
    return False

def set_webhook():
    webhook_url = f"{TELEGRAM_APP_URL}/{TELEGRAM_TOKEN}"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={webhook_url}"

    for _ in range(5):
        response = requests.get(url)
        if response.status_code == 200:
            logger.info(f"Webhook set successfully: {webhook_url}")
            return
        elif response.status_code == 429:
            retry_after = response.json().get('parameters', {}).get('retry_after', 1)
            logger.info(f"Too many requests. Retrying after {retry_after} seconds...")
            time.sleep(retry_after)
        else:
            logger.error(f"Failed to set webhook: {response.text}")
            return

if not check_webhook_status():
    set_webhook()

@app.route(f'/{TELEGRAM_TOKEN}/', methods=['POST'])
def webhook():
    try:
        req = request.get_json()
        logger.info(f'Received webhook request: {req}')
        bot.handle_message(req['message'])
        return 'Ok'
    except Exception as e:
        logger.error(f"Error handling webhook request: {e}")
        return 'Error', 500

@app.route('/results', methods=['POST'])
def handle_results():
    try:
        data = request.get_json()
        prediction_id = data['predictionId']
        mongo_client = pymongo.MongoClient(os.environ['MONGO_URI'])
        db = mongo_client['config']
        collection = db['image_collection']
        results = collection.find_one({'predictionId': prediction_id})

        if results:
            detected_objects = results['labels']
            results_text = f"Detected: {', '.join([obj['class'] for obj in detected_objects])}" if detected_objects else "No objects detected."
        else:
            results_text = "No predictions found."

        return results_text
    except Exception as e:
        logger.error(f"Error processing results request: {e}")
        return "Error", 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=30619, ssl_context=("polybot.crt", "privkey.pem"))
