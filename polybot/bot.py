import telebot
from loguru import logger
import os
import json
import boto3
import pymongo

# Function to get secrets from AWS Secrets Manager
def get_secret(secret_name):
    region_name = "eu-north-1"
    client = boto3.client("secretsmanager", region_name=region_name)

    try:
        response = client.get_secret_value(SecretId=secret_name)
        return json.loads(response['SecretString'])
    except Exception as e:
        logger.error(f"Error retrieving secret: {e}")
        return None

class ObjectDetectionBot:
    def __init__(self):
        # Load secrets from AWS Secrets Manager
        secrets = get_secret('polybot-secrets')
        if not secrets:
            raise RuntimeError("Failed to load secrets from AWS Secrets Manager")

        self.mongo_uri = secrets['MONGO_URI']
        self.mongo_db = secrets['MONGO_DB']
        self.mongo_collection = secrets['MONGO_COLLECTION']
        self.sqs_queue_url = secrets['SQS_QUEUE_URL']
        self.telegram_app_url = secrets['TELEGRAM_APP_URL']
        self.s3_bucket_name = secrets['S3_BUCKET_NAME']
        self.telegram_token = secrets['TELEGRAM_TOKEN']

        # Initialize Telegram bot
        self.telegram_bot_client = telebot.TeleBot(self.telegram_token)

        # AWS clients
        self.s3_client = boto3.client('s3', region_name='eu-north-1')
        self.sqs_client = boto3.client('sqs', region_name='eu-north-1')

        # MongoDB connection
        try:
            self.mongo_client = pymongo.MongoClient(self.mongo_uri)
            self.db = self.mongo_client[self.mongo_db]
            self.collection = self.db[self.mongo_collection]
        except pymongo.errors.PyMongoError as e:
            logger.error(f"Error connecting to MongoDB: {e}")
            raise

    def send_text(self, chat_id, text):
        try:
            self.telegram_bot_client.send_message(chat_id, text)
        except Exception as e:
            logger.error(f"Error sending message to chat {chat_id}: {e}")

    def download_user_photo(self, file_id):
        try:
            logger.info(f"Downloading photo with file_id: {file_id}")
            file_info = self.telegram_bot_client.get_file(file_id)
            data = self.telegram_bot_client.download_file(file_info.file_path)

            folder_name = 'photos'
            os.makedirs(folder_name, exist_ok=True)

            file_path = os.path.join(folder_name, f"{file_id}.jpg")
            with open(file_path, 'wb') as photo:
                photo.write(data)

            return file_path
        except Exception as e:
            logger.error(f"Error downloading photo: {e}")
            return None

    def upload_to_s3(self, file_path):
        try:
            s3_key = os.path.basename(file_path)
            self.s3_client.upload_file(file_path, self.s3_bucket_name, s3_key)
            return f'https://{self.s3_bucket_name}.s3.amazonaws.com/{s3_key}'
        except Exception as e:
            logger.error(f"Failed to upload {file_path} to S3: {e}")
            return None

    def send_to_sqs(self, img_name, s3_url):
        try:
            message_body = json.dumps({'imgName': img_name, 's3Url': s3_url})
            response = self.sqs_client.send_message(
                QueueUrl=self.sqs_queue_url,
                MessageBody=message_body
            )
            logger.info(f"Message sent to SQS: {response['MessageId']}")
        except Exception as e:
            logger.error(f"Failed to send message to SQS: {e}")

    def handle_message(self, msg):
        try:
            chat_id = msg.get('message', {}).get('chat', {}).get('id')
            if not chat_id:
                logger.error("No chat_id found.")
                return

            if 'photo' in msg.get('message', {}):
                file_id = msg['message']['photo'][-1].get('file_id')
                if not file_id:
                    self.send_text(chat_id, "Failed to process the image.")
                    return

                photo_path = self.download_user_photo(file_id)
                if not photo_path:
                    self.send_text(chat_id, "Error downloading image.")
                    return

                image_url = self.upload_to_s3(photo_path)
                if not image_url:
                    self.send_text(chat_id, "Error uploading image to S3.")
                    return

                self.send_text(chat_id, f"Image uploaded: {image_url}")
                self.send_to_sqs(os.path.basename(photo_path), image_url)

                os.remove(photo_path)
            else:
                self.send_text(chat_id, "I can only process photos. Please send a photo.")
        except Exception as e:
            logger.error(f"Error handling message: {e}")
