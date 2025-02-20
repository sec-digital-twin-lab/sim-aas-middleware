from __future__ import annotations

import os
from enum import Enum

from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.dor.default import DefaultDORService
from simaas.node.base import Node
from simaas.nodedb.default import DefaultNodeDBService
from simaas.rti.aws import AWSRTIService
from simaas.rti.default import DefaultRTIService

logger = Logging.get('node.default')

class DORType(Enum):
    NONE = 'none'
    BASIC = 'basic'


class RTIType(Enum):
    NONE = 'none'
    DOCKER = 'docker'


class DefaultNode(Node):
    def __init__(self, keystore: Keystore, datastore_path: str,
                 enable_db: bool, dor_type: DORType, rti_type: RTIType,
                 retain_job_history: bool = None, strict_deployment: bool = None) -> None:
        super().__init__(keystore)

        # create datastore (if it doesn't already exist)
        os.makedirs(datastore_path, exist_ok=True)

        self._datastore_path = datastore_path

        if enable_db:
            db_path = f"sqlite:///{os.path.join(self._datastore_path, 'node.db')}"
            logger.info(f"creating default NodeDB service using {db_path}.")
            self.db = DefaultNodeDBService(self, db_path)

        if dor_type == DORType.BASIC:
            db_path = f"sqlite:///{os.path.join(self._datastore_path, 'dor.db')}"
            logger.info(f"creating default DOR service using {db_path}.")
            self.dor = DefaultDORService(self, db_path)

        if rti_type == RTIType.DOCKER:
            db_path = f"sqlite:///{os.path.join(self._datastore_path, 'rti.db')}"
            self.rti = DefaultRTIService(self, db_path,
                                         retain_job_history=retain_job_history,
                                         strict_deployment=strict_deployment)

    @property
    def datastore(self) -> str:
        return self._datastore_path

    @classmethod
    def create(
            cls, keystore: Keystore, storage_path: str, p2p_address: str, rest_address: (str, int) = None,
            boot_node_address: (str, int) = None, bind_all_address: bool = False, enable_db: bool = True,
            dor_type: DORType = DORType.NONE, rti_type: RTIType = RTIType.NONE, retain_job_history: bool = False,
            strict_deployment: bool = True
    ) -> Node:
        node = DefaultNode(keystore, storage_path,
                           enable_db=enable_db, dor_type=dor_type, rti_type=rti_type,
                           retain_job_history=retain_job_history, strict_deployment=strict_deployment)
        node.startup(p2p_address, rest_address, boot_node_address, bind_all_address)

        return node
