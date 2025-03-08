import telebot
from loguru import logger
import os
import time
import requests
from telebot.types import InputFile
import boto3
from botocore.exceptions import BotoCoreError, NoCredentialsError

class ObjectDetectionBot:
    def __init__(self, token, telegram_app_url, s3_bucket_name):
        self.telegram_bot_client = telebot.TeleBot(token)
        self.s3_bucket_name = s3_bucket_name

        # Use AWS default credential chain instead of hardcoded env vars
        try:
            self.s3_client = boto3.client('s3', region_name='eu-north-1')
        except (BotoCoreError, NoCredentialsError) as e:
            logger.error(f"Failed to initialize S3 client: {e}")
            raise

        if not telegram_app_url:
            raise ValueError("TELEGRAM_APP_URL is missing")

        # Remove old webhooks and set the new one
        self.set_webhook(telegram_app_url, token)

    def set_webhook(self, telegram_app_url, token):
        """ Sets the Telegram bot webhook """
        try:
            self.telegram_bot_client.remove_webhook()
            time.sleep(0.5)
            webhook_url = f'{telegram_app_url}/{token}/'
            self.telegram_bot_client.set_webhook(url=webhook_url, timeout=60)
            logger.info(f'Webhook set: {webhook_url}')
        except Exception as e:
            logger.error(f"Failed to set webhook: {e}")

    def send_text(self, chat_id, text):
        """ Sends a text message to the Telegram chat """
        try:
            self.telegram_bot_client.send_message(chat_id, text)
        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"Failed to send message to chat {chat_id}. Error: {e}")
        except Exception as e:
            logger.error(f"Unknown error occurred while sending message to chat {chat_id}. Error: {e}")

    def send_text_with_quote(self, chat_id, text, quoted_msg_id):
        """ Sends a text message in reply to another message """
        try:
            self.telegram_bot_client.send_message(chat_id, text, reply_to_message_id=quoted_msg_id)
        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"Failed to send quoted message to chat {chat_id}. Error: {e}")

    def download_user_photo(self, msg):
        """ Downloads the latest photo from a user's message and saves it locally """
        try:
            if not msg.photo:
                raise RuntimeError("Message does not contain a photo")

            file_info = self.telegram_bot_client.get_file(msg.photo[-1].file_id)
            data = self.telegram_bot_client.download_file(file_info.file_path)

            folder_name = 'photos'
            os.makedirs(folder_name, exist_ok=True)

            file_name = f"{int(time.time())}_{os.path.basename(file_info.file_path)}"  # Unique file names
            file_path = os.path.join(folder_name, file_name)

            with open(file_path, 'wb') as photo:
                photo.write(data)

            logger.info(f"Photo saved at {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Failed to download photo: {e}")
            raise

    def send_photo(self, chat_id, img_path):
        """ Sends a photo to a Telegram chat """
        try:
            if not os.path.exists(img_path):
                raise RuntimeError(f"Image path does not exist: {img_path}")

            with open(img_path, 'rb') as img:
                self.telegram_bot_client.send_photo(chat_id, img)
                logger.info(f"Photo sent successfully to chat {chat_id}")
        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"Failed to send photo to chat {chat_id}. Error: {e}")
        except Exception as e:
            logger.error(f"Error sending photo to chat {chat_id}. Error: {e}")

    def upload_to_s3(self, file_path):
        """ Uploads an image to S3 and returns the URL """
        try:
            s3_key = os.path.basename(file_path)
            self.s3_client.upload_file(file_path, self.s3_bucket_name, s3_key)
            image_url = f"https://{self.s3_bucket_name}.s3.amazonaws.com/{s3_key}"
            logger.info(f"Uploaded {file_path} to S3: {image_url}")
            return image_url
        except (BotoCoreError, NoCredentialsError) as e:
            logger.error(f"Failed to upload {file_path} to S3. AWS credentials error: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to upload {file_path} to S3. Error: {e}")
            raise

    def get_yolo5_results(self, img_name):
        """ Sends the image name to YOLOv5 for object detection """
        try:
            logger.info(f'Sending imgName to YOLOv5: {img_name}')
            response = requests.post("http://yolov5-service:8081/predict", json={"imgName": img_name})
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"YOLOv5 service error: {e}")
            return None

    def handle_message(self, msg):
        """ Handles incoming Telegram messages """
        try:
            chat_id = msg.chat.id
            logger.info(f'Received message from chat ID: {chat_id}')

            if msg.photo:
                try:
                    logger.info('Downloading user photo...')
                    photo_path = self.download_user_photo(msg)

                    logger.info('Uploading to S3...')
                    image_url = self.upload_to_s3(photo_path)
                    self.send_text(chat_id, f"Image uploaded: {image_url}")

                    logger.info('Sending to YOLOv5...')
                    img_name = os.path.basename(photo_path)
                    yolo_results = self.get_yolo5_results(img_name)

                    if yolo_results and 'predictions' in yolo_results:
                        detected_objects = [obj['class'] for obj in yolo_results['predictions']]
                        results_text = f"Detected: {', '.join(detected_objects)}" if detected_objects else "No objects detected."
                    else:
                        results_text = "Error processing the image."

                    self.send_text(chat_id, results_text)

                except Exception as e:
                    logger.error(f"Processing error: {e}")
                    self.send_text(chat_id, f"Error processing the image: {str(e)}")
            else:
                self.send_text(chat_id, "Please send a photo.")

        except Exception as e:
            logger.error(f"Error handling message: {e}")