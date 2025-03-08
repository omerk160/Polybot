def handle_message(self, msg):
    try:
        logger.info(f'Incoming message from chat ID: {msg["chat"]["id"]}')

        if self.is_current_msg_photo(msg):
            try:
                logger.info('Downloading user photo...')
                photo_path = self.download_user_photo(msg)
                logger.info(f'Photo saved at {photo_path}')

                logger.info('Uploading to S3...')
                image_url = self.upload_to_s3(photo_path)
                self.send_text(msg["chat"]["id"], f"Image uploaded: {image_url}")

                logger.info('Sending to YOLOv5...')
                img_name = os.path.basename(photo_path)
                yolo_results = self.get_yolo5_results(img_name)

                if yolo_results and 'predictions' in yolo_results:
                    detected_objects = [obj['class'] for obj in yolo_results['predictions']]
                    results_text = f"Detected: {', '.join(detected_objects)}" if detected_objects else "No objects detected."
                else:
                    results_text = "Error processing the image."

                self.send_text(msg["chat"]["id"], results_text)

            except Exception as e:
                logger.error(f"Processing error: {e}")
                self.send_text(msg["chat"]["id"], "Error processing the image.")
        else:
            self.send_text(msg["chat"]["id"], "Please send a photo.")
    except telebot.apihelper.ApiTelegramException as e:
        logger.error(f"Telegram API error: {e}")
        # Handle the error by sending a generic error message or logging it
        try:
            self.send_text(msg["chat"]["id"], "An error occurred while processing your message.")
        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"Failed to send error message: {e}")
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        # Handle the error by sending a generic error message or logging it
        try:
            self.send_text(msg["chat"]["id"], "An error occurred while processing your message.")
        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"Failed to send error message: {e}")
