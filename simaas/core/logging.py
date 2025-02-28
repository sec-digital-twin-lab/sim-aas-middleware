import logging
import time
from logging.handlers import RotatingFileHandler
from threading import Lock

import os
import boto3
from botocore.exceptions import NoCredentialsError
from watchtower import CloudWatchLogHandler

class Logging:
    _lock: Lock = Lock()
    _default_format: str = '%(asctime)s.%(msecs)03d [%(levelname)s] [%(name)s] %(message)s'
    _default_datefmt: str = '%Y-%m-%d %H:%M:%S'

    @classmethod
    def initialise(cls, level: int = logging.INFO, log_path: str = None, console_log_enabled: bool = True, 
                   custom_format: str = None, custom_datefmt: str = None,
                   max_bytes: int = 1*1024*1024, backup_count: int = 10, log_to_aws: bool = False) -> None:
        with Logging._lock:
            # set formatting and use GMT/UTC timezone
            formatter = logging.Formatter(
                fmt=custom_format if custom_format else Logging._default_format,
                datefmt=custom_datefmt if custom_datefmt else Logging._default_datefmt
            )
            formatter.converter = time.gmtime

            # set default log level
            root_logger = logging.getLogger()
            root_logger.setLevel(level)

            # remove all handlers -> we will create our own
            cls.remove_all_handlers()

            # do we have a default log path?
            if log_path:
                file_handler = RotatingFileHandler(log_path, maxBytes=max_bytes, backupCount=backup_count)
                file_handler.setFormatter(formatter)
                root_logger.addHandler(file_handler)

            # do we have console logging enabled?
            if console_log_enabled:
                console_handler = logging.StreamHandler()
                console_handler.setFormatter(formatter)
                root_logger.addHandler(console_handler)

            # do we have AWS logging enabled?
            if log_to_aws:
                # do we have all the variables?
                required = ['SIMAAS_AWS_REGION', 'SIMAAS_AWS_LOG_GROUP_NAME', 'SIMAAS_AWS_TASK_ID']
                if not all(var in os.environ for var in required):
                    root_logger.error(f"Required environment variables not defined: {required}")

                else:
                    region_name = os.environ['SIMAAS_AWS_REGION']
                    log_group_name = os.environ['SIMAAS_AWS_LOG_GROUP_NAME']
                    log_stream_name = os.environ['SIMAAS_AWS_TASK_ID']
                    boto3.setup_default_session(region_name=region_name)

                    try:
                        cloudwatch_handler = CloudWatchLogHandler(log_group_name=log_group_name, log_stream_name=log_stream_name)
                        cloudwatch_handler.setFormatter(formatter)
                        root_logger.addHandler(cloudwatch_handler)
                    except NoCredentialsError:
                        root_logger.error("No credentials found for AWS cloud watch.")

    @classmethod
    def get(cls, name: str, level: int = None, custom_log_path: str = None) -> logging.Logger:
        logger = logging.getLogger(name)

        # do we have a custom level?
        if level:
            logger.setLevel(level)

        # do we have a custom log path?
        if custom_log_path:
            file_handler = logging.FileHandler(custom_log_path)
            file_handler.setFormatter(logging.Formatter(Logging._default_format))
            logger.addHandler(file_handler)

        return logger

    @staticmethod
    def remove_all_handlers():
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            # Flush and close any open streams
            handler.flush()
            handler.close()
            root_logger.removeHandler(handler)
