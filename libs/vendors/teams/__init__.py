import logging

import requests

from libs.utils.config import TEAMS_WEBHOOK_URL

teams_webhook_url = TEAMS_WEBHOOK_URL


class TeamsLogger(logging.Handler):

    def emit(self, record):
        if record.levelno == logging.ERROR:
            payload = {
                "type": "message",
                "attachments": [
                    {
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": {
                            "type": "AdaptiveCard",
                            "version": "1.0",
                            "body": [
                                {
                                    "type": "TextBlock",
                                    "text": "**Error Log Notification**",
                                    "size": "Large",
                                    "weight": "Bolder",
                                    "color": "Attention",
                                },
                                {
                                    "type": "FactSet",
                                    "facts": [
                                        {
                                            "title": "Request ID:",
                                            "value": record.request_id,
                                        },
                                        {
                                            "title": "Request route:",
                                            "value": record.request_route,
                                        },
                                        {
                                            "title": "Error Message:",
                                            "value": record.getMessage(),
                                        },
                                    ],
                                },
                            ],
                        },
                    }
                ],
            }

            response = requests.post(teams_webhook_url, json=payload)
            if response.status_code not in [202, 200]:
                print(
                    f"Error sending log to Teams: {response.text}, status code: {response.status_code}"
                )
