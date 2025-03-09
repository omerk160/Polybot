import telebot
from loguru import logger
import os
import time
import requests
from telebot.types import InputFile
import boto3
import telebot.types
import json



class ObjectDetectionBot:
    def __init__(self, token, telegram_app_url, s3_bucket_name):
        self.telegram_bot_client = telebot.TeleBot(token)
        self.s3_bucket_name = s3_bucket_name
        self.s3_client = boto3.client('s3',
                                      aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                                      aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
                                      region_name='eu-north-1')

        if not telegram_app_url:
            raise ValueError("TELEGRAM_APP_URL is missing")

        # Remove old webhooks and set the new one
        try:
            self.telegram_bot_client.remove_webhook()
            time.sleep(0.5)
            webhook_url = f'{telegram_app_url}/{token}/'
            self.telegram_bot_client.set_webhook(url=webhook_url, timeout=60)
            logger.info(f'Webhook set: {webhook_url}')
        except Exception as e:
            logger.error(f"Failed to set webhook: {e}")

    def send_text(self, chat_id, text):
        try:
            self.telegram_bot_client.send_message(chat_id, text)
        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"Failed to send message to chat {chat_id}. Error: {e}")
        except Exception as e:
            logger.error(f"Unknown error occurred while sending message to chat {chat_id}. Error: {e}")

    def is_current_msg_photo(self, msg):
        return msg.photo is not None

    def download_user_photo(self, msg):
        if not msg.photo:
            raise RuntimeError('Message does not contain a photo')

        file_id = msg.photo[-1].file_id
        file_info = self.telegram_bot_client.get_file(file_id)
        data = self.telegram_bot_client.download_file(file_info.file_path)

        folder_name = 'photos'
        os.makedirs(folder_name, exist_ok=True)

        file_path = os.path.join(folder_name, os.path.basename(file_info.file_path))
        with open(file_path, 'wb') as photo:
            photo.write(data)

        return file_path

    def send_photo(self, chat_id, img_path):
        if not os.path.exists(img_path):
            logger.error(f"Image path does not exist: {img_path}")
            return

        with open(img_path, 'rb') as img:
            self.telegram_bot_client.send_photo(chat_id, img)

    def upload_to_s3(self, file_path):
        try:
            s3_key = os.path.basename(file_path)
            self.s3_client.upload_file(file_path, self.s3_bucket_name, s3_key)
            s3_url = f'https://{self.s3_bucket_name}.s3.amazonaws.com/{s3_key}'
            logger.info(f"Uploaded to S3: {s3_url}")
            return s3_url
        except Exception as e:
            logger.error(f"Failed to upload {file_path} to S3. Error: {e}")
            return None

    def send_to_sqs(self, img_name, s3_url):
        sqs_client = boto3.client('sqs', region_name='eu-north-1')
    try:
        response = sqs_client.send_message(
            QueueUrl=os.environ['SQS_QUEUE_URL'],
            MessageBody=json.dumps({'imgName': img_name, 's3Url': s3_url}),
            MessageGroupId='image-processing',  # If using FIFO
        )
        logger.info(f"Message sent to SQS: {response['MessageId']}")
    except Exception as e:
        logger.error(f"Failed to send message to SQS. Error: {e}")

    def get_yolo5_results(self, img_name):
        try:
            logger.info(f'Sending imgName to YOLOv5: {img_name}')
            YOLOV5_URL = os.getenv("YOLOV5_URL", "http://yolo5-service:5000")
            response = requests.post(f"{YOLOV5_URL}/predict", json={"imgName": img_name})

            # Log the response status and any errors
            if response.status_code == 200:
                logger.info(f"YOLOv5 prediction result: {response.json()}")
            else:
                logger.error(f"Error in YOLOv5 request: {response.status_code} - {response.text}")

            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"YOLOv5 service error: {e}")
            return None

    def handle_message(self, msg):
        try:
            # Convert dictionary to a Message object
            if isinstance(msg, dict):
                msg = telebot.types.Message.de_json(msg)

            chat_id = msg.chat.id  # Now this will work correctly
            logger.info(f"Handling message from chat ID: {chat_id}")

            if msg.photo:
                try:
                    logger.info('Downloading user photo...')
                    photo_path = self.download_user_photo(msg)
                    logger.info(f'Photo saved at {photo_path}')

                    logger.info('Uploading to S3...')
                    image_url = self.upload_to_s3(photo_path)
                    logger.info(f"Image uploaded to S3: {image_url}")  # Add this log
                    if not image_url:
                        self.send_text(chat_id, "Failed to upload image to S3.")
                        return

                    self.send_text(chat_id, f"Image uploaded: {image_url}")
                    self.send_to_sqs(os.path.basename(photo_path), image_url)

                    logger.info('Sending to YOLOv5...')
                    img_name = os.path.basename(photo_path)
                    yolo_results = self.get_yolo5_results(img_name)

                    if yolo_results and 'predictions' in yolo_results:
                        detected_objects = [obj['class'] for obj in yolo_results['predictions']]
                        results_text = f"Detected: {', '.join(detected_objects)}" if detected_objects else "No objects detected."
                    elif yolo_results is None:
                        results_text = "Error processing the image."
                    else:
                        results_text = "No predictions found."

                    self.send_text(chat_id, results_text)

                except Exception as e:
                    logger.error(f"Processing error: {e}")
                    self.send_text(chat_id, f"Error processing the image: {str(e)}")
            else:
                self.send_text(chat_id, "Please send a photo.")

        except Exception as e:
            logger.error(f"Error handling message: {e}")