import telebot
from loguru import logger
import os
import time
import requests
from telebot.types import InputFile
import boto3

class ObjectDetectionBot:
    def __init__(self, token, telegram_chat_url, s3_bucket_name, s3_client):
        self.telegram_bot_client = telebot.TeleBot(token)
        self.s3_bucket_name = s3_bucket_name
        self.s3_client = s3_client

        # Use the existing S3 client from environment variables
        self.s3_client = boto3.client('s3', aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
                                      aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'],
                                      region_name='eu-north-1')

        # Get the Telegram App URL from environment variables
        ngrok_url = os.getenv('TELEGRAM_APP_URL')

        if not ngrok_url:
            raise ValueError("TELEGRAM_APP_URL is missing")

        # Remove any existing webhooks and set a new webhook URL
        self.telegram_bot_client.remove_webhook()
        time.sleep(0.5)
        self.telegram_bot_client.set_webhook(url=f'{ngrok_url}/{token}/', timeout=60)

        logger.info(f'Telegram Bot information\n\n{self.telegram_bot_client.get_me()}')

    def send_text(self, chat_id, text):
        self.telegram_bot_client.send_message(chat_id, text)

    def send_text_with_quote(self, chat_id, text, quoted_msg_id):
        self.telegram_bot_client.send_message(chat_id, text, reply_to_message_id=quoted_msg_id)

    def is_current_msg_photo(self, msg):
        return 'photo' in msg

    def download_user_photo(self, msg):
        if not self.is_current_msg_photo(msg):
            raise RuntimeError(f'Message content of type "photo" expected')

        file_info = self.telegram_bot_client.get_file(msg['photo'][-1]['file_id'])
        data = self.telegram_bot_client.download_file(file_info.file_path)
        folder_name = 'photos'

        if not os.path.exists(folder_name):
            os.makedirs(folder_name)

        file_path = os.path.join(folder_name, file_info.file_path.split('/')[-1])
        with open(file_path, 'wb') as photo:
            photo.write(data)

        return file_path

    def send_photo(self, chat_id, img_path):
        if not os.path.exists(img_path):
            raise RuntimeError("Image path doesn't exist")

        self.telegram_bot_client.send_photo(chat_id, InputFile(img_path))

    def upload_to_s3(self, file_path):
        s3_key = os.path.basename(file_path)
        self.s3_client.upload_file(file_path, self.s3_bucket_name, s3_key)
        s3_url = f'https://{self.s3_bucket_name}.s3.amazonaws.com/{s3_key}'
        return s3_url

    def get_yolo5_results(self, img_name):
        try:
            logger.info(f'Sending imgName to YOLOv5 service: {img_name}')
            logger.debug(f'Preparing to send POST request to http://yolov5-service:8081/predict with payload: {{"imgName": "{img_name}"}}')
            response = requests.post(
                "http://yolov5-service:8081/predict",
                json={"imgName": img_name}
            )
            logger.debug(f'Response status code from YOLOv5 service: {response.status_code}')
            response.raise_for_status()
            logger.info(f'Received response from YOLOv5 service: {response.json()}')
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error contacting YOLOv5 microservice: {e}")
            logger.debug(f'Error details: {e.__class__} - {str(e)}')
            return None

    def handle_message(self, msg):
        logger.info(f'Incoming message: {msg}')
        chat_id = msg['chat']['id']

        if self.is_current_msg_photo(msg):
            try:
                logger.info('Step 1: Downloading user photo')
                photo_path = self.download_user_photo(msg)
                logger.info(f'Photo downloaded to: {photo_path}')

                logger.info('Step 2: Uploading photo to S3')
                image_url = self.upload_to_s3(photo_path)
                logger.info(f'Photo uploaded to S3: {image_url}')
                self.send_text(chat_id, "Image uploaded to S3. Processing...")

                img_name = os.path.basename(photo_path)
                logger.info(f'Image name for YOLOv5: {img_name}')

                logger.info('Step 4: Sending image to YOLOv5 service')
                yolo_results = self.get_yolo5_results(img_name)
                logger.info(f'YOLOv5 results: {yolo_results}')

                if yolo_results:
                    logger.info('Step 5: Sending prediction results to user')
                    if 'predictions' in yolo_results and yolo_results['predictions']:
                        detected_objects = [label['class'] for label in yolo_results['predictions']]
                        if detected_objects:
                            results_text = f"Detected objects: {', '.join(detected_objects)}"
                        else:
                            results_text = "No objects detected."
                        self.send_text(chat_id, results_text)
                    else:
                        logger.error('YOLOv5 service returned no predictions')
                        self.send_text(chat_id, "There was an error processing the image.")
                else:
                    logger.error('YOLOv5 service returned no results or incorrect response format')
                    self.send_text(chat_id, "There was an error processing the image.")
            except Exception as e:
                logger.error(f"Error processing image: {e}")
                self.send_text(chat_id, "There was an error processing the image.")
        else:
            logger.info('Message does not contain a photo')
            self.send_text(chat_id, "Please send a photo with a valid caption.")

# Initialize the bot with environment variables
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
TELEGRAM_APP_URL = os.environ['TELEGRAM_APP_URL']
S3_BUCKET_NAME = os.environ['S3_BUCKET_NAME']

bot = ObjectDetectionBot(TELEGRAM_TOKEN, TELEGRAM_APP_URL, S3_BUCKET_NAME, None)

# Start the bot
bot.telegram_bot_client.polling()
