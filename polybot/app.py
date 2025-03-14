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

secrets_client = boto3.client('secretsmanager', region_name="eu-north-1")

try:
    response = secrets_client.get_secret_value(SecretId="polybot-secrets")
    secrets = json.loads(response['SecretString'])

    os.environ["TELEGRAM_TOKEN"] = secrets["TELEGRAM_TOKEN"]
    os.environ["S3_BUCKET_NAME"] = secrets["S3_BUCKET_NAME"]
    os.environ["SQS_QUEUE_URL"] = secrets["SQS_QUEUE_URL"]
    os.environ["TELEGRAM_APP_URL"] = secrets["TELEGRAM_APP_URL"]
    os.environ["MONGO_URI"] = secrets["MONGO_URI"]
    os.environ["MONGO_DB"] = "config"
    os.environ["MONGO_COLLECTION"] = "image_collection"
except Exception as e:
    logger.error(f"Error fetching secrets: {e}")
    raise RuntimeError("Unable to fetch secrets from AWS Secrets Manager.")

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
S3_BUCKET_NAME = os.environ["S3_BUCKET_NAME"]
SQS_QUEUE_URL = os.environ["SQS_QUEUE_URL"]
TELEGRAM_APP_URL = os.environ["TELEGRAM_APP_URL"]

s3_client = boto3.client('s3')

mongo_client = pymongo.MongoClient(os.environ['MONGO_URI'])
app.mongo_db = mongo_client[os.environ['MONGO_DB']]
app.mongo_collection = app.mongo_db[os.environ['MONGO_COLLECTION']]
logger.info("MongoDB connection initialized")

bot = ObjectDetectionBot(TELEGRAM_TOKEN, S3_BUCKET_NAME)

def check_webhook_status():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getWebhookInfo"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            webhook_info = response.json()
            if webhook_info['result']['url']:
                logger.info(f"Webhook is already set to: {webhook_info['result']['url']}")
                return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Error checking webhook status: {e}")
    return False

def set_webhook():
    webhook_url = f"{TELEGRAM_APP_URL}/{TELEGRAM_TOKEN}"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={webhook_url}"

    for _ in range(5):
        try:
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
        except requests.exceptions.RequestException as e:
            logger.error(f"Error setting webhook: {e}")
            time.sleep(2)

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
        prediction_id = data.get('predictionId')
        logger.info(f"Processing predictionId: {prediction_id}")

        if not prediction_id:
            logger.error("No predictionId provided in the request.")
            return "Prediction ID missing", 400

        results = app.mongo_collection.find_one({'_id': prediction_id})
        logger.info(f"Retrieved from MongoDB: {results}")

        if results:
            detected_objects = results.get('labels', [])
            chat_id = results.get('chat_id')
            predicted_img_path = results.get('predicted_img_path')
            original_img_path = results.get('original_img_path')

            if not chat_id:
                logger.error(f"No chat_id in prediction: {prediction_id}")
                return "Chat ID missing", 500

            # Plain text message
            if detected_objects:
                objects_list = "\n".join([f"- {obj['class']} ({float(obj['cx'] + obj['width']) * 100:.1f}%)" for obj in detected_objects])
                results_text = (
                    f"Image Analysis Complete!\n"
                    f"Image: {original_img_path}\n"
                    f"Detected:\n{objects_list}\n"
                    f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
                )
            else:
                results_text = (
                    f"Image Analysis Complete!\n"
                    f"Image: {original_img_path}\n"
                    f"No objects detected.\n"
                    f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
                )

            logger.info(f"Sending text: {results_text}")
            bot.send_text(chat_id, results_text)  # Plain text

            if predicted_img_path:
                local_img_path = f"/tmp/{prediction_id}.jpg"
                try:
                    s3_client.download_file(S3_BUCKET_NAME, predicted_img_path, local_img_path)
                    bot.send_photo(chat_id, local_img_path, caption="Here’s your image with detected objects!")
                    os.remove(local_img_path)
                except Exception as e:
                    logger.error(f"Failed to send image: {e}")
                    bot.send_text(chat_id, "Error sending predicted image.")
        else:
            logger.warning(f"No prediction found for ID: {prediction_id}")
            return "No predictions found", 404

        return "Results sent to Telegram", 200
    except Exception as e:
        logger.error(f"Error in /results: {e}")
        return "Error", 500

if __name__ == "__main__":
    try:
        app.run(host="0.0.0.0", port=30184)
    except Exception as e:
        logger.error(f"Failed to start the Flask app: {e}")