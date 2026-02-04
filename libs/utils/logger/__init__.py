import json
import logging
import os
import zipfile
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler

from colorlog import ColoredFormatter

from libs.utils import config
from libs.vendors import teams

# Context variable to store request path
request_path_var = ContextVar("request_route", default="-")
request_id_var = ContextVar("request_id", default="-")


def create_folder_if_not_exists(folder_path):
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)


class ContextFilter(logging.Filter):
    """Enhances log messages with contextual information"""

    def filter(self, record):
        try:
            record.request_id = request_id_var.get()
            record.request_route = request_path_var.get()
        except RuntimeError:
            record.request_route = ""
            record.request_id = ""
        return True


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord):
        log_record = {
            "level": record.levelname,
            "app_name": record.name,
            "file_name": record.filename,
            "request_route": record.request_route,
            "func_name": record.funcName,
            "message": record.getMessage(),
        }
        return json.dumps(log_record)


class FileLogger:
    def __init__(self, app_name):
        self.app_name = app_name
        self.log_file_path = os.path.join("logs", f"{app_name}.log")
        create_folder_if_not_exists("logs")

    def get_handler(self):
        handler = RotatingFileHandler(
            self.log_file_path, backupCount=10, maxBytes=10240
        )
        handler.namer = lambda name: name.replace(".log", "") + ".zip"
        handler.rotator = self._rotator
        handler.setFormatter(JSONFormatter())
        handler.addFilter(ContextFilter())
        return handler

    @staticmethod
    def _rotator(source, dest):
        zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED).write(
            source, os.path.basename(source)
        )
        os.remove(source)


class ConsoleLogger:
    def __init__(self, app_name):
        self.app_name = app_name

    def get_handler(self):
        formatter = ColoredFormatter(
            f"%(log_color)s%(asctime)s - {self.app_name} - %(request_route)s - %(levelname)s - [%(pathname)s:%(lineno)d] - %(message)s%(reset)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            reset=True,
            log_colors={
                "DEBUG": "green",
                "INFO": "blue",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "red,bg_white",
            },
        )
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        handler.addFilter(ContextFilter())
        return handler


def setup_logger(
    app_name,
    enable_console_logger: bool,
    enable_file_logger: bool,
    enable_teams_logger: bool,
    enable_slack_logger: bool,
    enable_cloudwatch_logger: bool = False,
):
    logger = logging.getLogger(app_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if enable_file_logger:
        logger.addHandler(FileLogger(app_name).get_handler())
    if enable_console_logger:
        logger.addHandler(ConsoleLogger(app_name).get_handler())
    if enable_teams_logger:
        logger.addHandler(teams.TeamsLogger())
    if enable_slack_logger:
        pass
    if enable_cloudwatch_logger:
        pass

    return logger
