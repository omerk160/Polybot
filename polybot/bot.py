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
    def __init__(self, telegram_token: str, s3_bucket_name: str):
        secrets = get_secret('polybot-secrets')
        if secrets:
            self.mongo_uri = secrets['MONGO_URI']
            self.mongo_db = secrets['MONGO_DB']
            self.mongo_collection = secrets['MONGO_COLLECTION']
            self.sqs_queue_url = secrets['SQS_QUEUE_URL']
            self.telegram_app_url = secrets['TELEGRAM_APP_URL']
            self.s3_bucket_name = secrets['S3_BUCKET_NAME']
            self.telegram_token = secrets['TELEGRAM_TOKEN']
        else:
            raise RuntimeError("Failed to load secrets from AWS Secrets Manager")

        self.telegram_bot_client = telebot.TeleBot(telegram_token)
        self.s3_bucket_name = s3_bucket_name
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

    def send_text(self, chat_id, text, parse_mode=None):
        try:
            self.telegram_bot_client.send_message(chat_id, text, parse_mode=parse_mode)
        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"Failed to send message to chat {chat_id}. Error: {e}")
        except Exception as e:
            logger.error(f"Unknown error occurred while sending message to chat {chat_id}. Error: {e}")

    def is_current_msg_photo(self, msg):
        return bool(msg.get('photo', None))

    def download_user_photo(self, file_id):
        logger.info(f"Downloading photo with file_id: {file_id}")
        try:
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

    def send_photo(self, chat_id, img_path, caption=None):
        if not os.path.isfile(img_path):
            logger.error(f"Image path does not exist: {img_path}")
            return
        try:
            with open(img_path, 'rb') as img:
                self.telegram_bot_client.send_photo(chat_id, img, caption=caption)
                logger.info(f"Photo sent to chat_id: {chat_id}")
        except Exception as e:
            logger.error(f"Error sending photo to chat_id {chat_id}: {e}")

    def get_next_image_number(self):
        # Find the highest image number in MongoDB
        latest = self.collection.find_one(sort=[("image_number", pymongo.DESCENDING)])
        if latest and "image_number" in latest:
            return latest["image_number"] + 1
        return 1  # Start at 1 if no images exist

    def upload_to_s3(self, file_path):
        try:
            image_number = self.get_next_image_number()
            s3_key = f"image_{image_number}.jpg"
            self.s3_client.upload_file(file_path, self.s3_bucket_name, s3_key)
            url = f'https://{self.s3_bucket_name}.s3.amazonaws.com/{s3_key}'
            # Store the image number in MongoDB for tracking
            self.collection.update_one(
                {"original_img_path": s3_key},
                {"$set": {"image_number": image_number}},
                upsert=True
            )
            return url
        except Exception as e:
            logger.error(f"Failed to upload {file_path} to S3. Error: {e}")
            return None

    def send_to_sqs(self, img_name, s3_url, chat_id):
        try:
            message_body = json.dumps({'imgName': img_name, 'chat_id': chat_id, 's3Url': s3_url})
            response = self.sqs_client.send_message(
                QueueUrl=self.sqs_queue_url,
                MessageBody=message_body
            )
            logger.info(f"Message sent to SQS: {response['MessageId']}")
        except Exception as e:
            logger.error(f"Failed to send message to SQS. Error: {e}")

    def handle_message(self, msg):
        try:
            logger.info(f"Handling message: {msg}")
            chat_id = msg.get('chat', {}).get('id')
            if not chat_id:
                logger.error("Failed to retrieve chat ID.")
                return

            logger.info(f"Handling message from chat ID: {chat_id}")

            if 'photo' in msg:
                try:
                    file_id = msg['photo'][-1].get('file_id')
                    if not file_id:
                        logger.error("No file_id found for the photo.")
                        self.send_text(chat_id, "Failed to process the image.")
                        return

                    logger.info('Downloading user photo...')
                    photo_path = self.download_user_photo(file_id)
                    if not photo_path:
                        self.send_text(chat_id, "Error downloading the photo.")
                        return
                    logger.info(f'Photo saved at {photo_path}')

                    logger.info('Uploading to S3...')
                    image_url = self.upload_to_s3(photo_path)
                    if not image_url:
                        self.send_text(chat_id, "Failed to upload image to S3.")
                        return

                    logger.info(f"Image uploaded to S3: {image_url}")
                    self.send_text(chat_id, f"📤 *Image Uploaded Successfully!*\nURL: {image_url}", parse_mode='Markdown')
                    self.send_to_sqs(os.path.basename(photo_path), image_url, chat_id)

                    os.remove(photo_path)
                except Exception as e:
                    logger.error(f"Processing error: {e}")
                    self.send_text(chat_id, f"Error processing the image: {str(e)}")
            else:
                self.send_text(chat_id, "I can only process photos. Please send a photo.")
        except Exception as e:
            logger.error(f"Error handling message: {e}")