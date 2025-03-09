import telebot
from loguru import logger
import os
import time
import requests
from telebot.types import InputFile
import boto3
import telebot.types
import json
import pymongo

class ObjectDetectionBot:
    def __init__(self, token, telegram_app_url, s3_bucket_name):
        self.telegram_bot_client = telebot.TeleBot(token)
        self.s3_bucket_name = s3_bucket_name
        self.s3_client = boto3.client('s3',
                                      aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                                      aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
                                      region_name='eu-north-1')

        self.mongo_client = pymongo.MongoClient(os.environ['MONGO_URI'])
        self.db = self.mongo_client[os.environ['MONGO_DB']]
        self.collection = self.db[os.environ['MONGO_COLLECTION']]

        if not telegram_app_url:
            raise ValueError("TELEGRAM_APP_URL is missing")

        try:
            self.telegram_bot_client.remove_webhook()
            time.sleep(0.5)
            webhook_url = f'{telegram_app_url}/{token}/'
            self.telegram_bot_client.set_webhook(url=webhook_url, timeout=60)
            logger.info(f'Webhook set: {webhook_url}')
        except Exception as e:
            logger.error(f"Failed to set webhook: {e}")

    def handle_results(self, prediction_id):
        try:
            results = self.collection.find_one({'predictionId': prediction_id})
            if results:
                detected_objects = results['labels']
                return f"Detected: {', '.join([obj['class'] for obj in detected_objects])}" if detected_objects else "No objects detected."
            else:
                return "No predictions found."
        except Exception as e:
            logger.error(f"Error retrieving results: {e}")
            return "Error processing the request."

app = Flask(__name__)

@app.route('/results', methods=['POST'])
def handle_results():
    try:
        data = request.get_json()
        prediction_id = data['predictionId']
        result_text = bot.handle_results(prediction_id)
        return result_text
    except Exception as e:
        logger.error(f"Error processing results request: {e}")
        return "Error", 500

if __name__ == "__main__":
    app.run(debug=True)
