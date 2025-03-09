import telebot
from loguru import logger
import os
import time
import requests
from telebot.types import InputFile
import boto3
import pymongo
import json

class ObjectDetectionBot:
    def __init__(self, token, s3_bucket_name):
        self.telegram_bot_client = telebot.TeleBot(token)
        self.s3_bucket_name = s3_bucket_name
        self.s3_client = boto3.client('s3', region_name='eu-north-1')

        self.mongo_client = pymongo.MongoClient(os.environ['MONGO_URI'])
        self.db = self.mongo_client[os.environ['MONGO_DB']]
        self.collection = self.db[os.environ['MONGO_COLLECTION']]

    def send_text(self, chat_id, text):
        try:
            self.telegram_bot_client.send_message(chat_id, text)
        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"Failed to send message to chat {chat_id}. Error: {e}")
        except Exception as e:
            logger.error(f"Unknown error occurred while sending message to chat {chat_id}. Error: {e}")

    def is_current_msg_photo(self, msg):
        return bool(msg.photo)

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
            return f'https://{self.s3_bucket_name}.s3.amazonaws.com/{s3_key}'
        except Exception as e:
            logger.error(f"Failed to upload {file_path} to S3. Error: {e}")
            return None

    def send_to_sqs(self, img_name, s3_url):
        sqs_client = boto3.client('sqs', region_name='eu-north-1')
        try:
            response = sqs_client.send_message(
                QueueUrl=os.environ['SQS_QUEUE_URL'],
                MessageBody=json.dumps({'imgName': img_name, 's3Url': s3_url}),
                MessageGroupId='image-processing',
            )
            logger.info(f"Message sent to SQS: {response['MessageId']}")
        except Exception as e:
            logger.error(f"Failed to send message to SQS. Error: {e}")

    def handle_message(self, msg):
        try:
            chat_id = msg.chat.id
            logger.info(f"Handling message from chat ID: {chat_id}")

            if self.is_current_msg_photo(msg):
                try:
                    logger.info('Downloading user photo...')
                    photo_path = self.download_user_photo(msg)
                    logger.info(f'Photo saved at {photo_path}')

                    logger.info('Uploading to S3...')
                    image_url = self.upload_to_s3(photo_path)
                    logger.info(f"Image uploaded to S3: {image_url}")
                    if not image_url:
                        self.send_text(chat_id, "Failed to upload image to S3.")
                        return

                    self.send_text(chat_id, f"Image uploaded: {image_url}")
                    self.send_to_sqs(os.path.basename(photo_path), image_url)

                except Exception as e:
                    logger.error(f"Processing error: {e}")
                    self.send_text(chat_id, f"Error processing the image: {str(e)}")
            else:
                self.send_text(chat_id, "Please send a photo.")

        except Exception as e:
            logger.error(f"Error handling message: {e}")

