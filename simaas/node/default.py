from __future__ import annotations

import os
from typing import Optional, Type

from simaas.core.async_helpers import run_coro_safely
from simaas.core.keystore import Keystore
from simaas.core.logging import get_logger
from simaas.node.base import Node
from simaas.nodedb.default import DefaultNodeDBService

log = get_logger('simaas.node', 'node')


class DefaultNode(Node):
    def __init__(self, keystore: Keystore, datastore_path: str,
                 enable_db: bool, dor_plugin_class: Optional[Type] = None,
                 rti_plugin_class: Optional[Type] = None,
                 retain_job_history: bool = None, strict_deployment: bool = None) -> None:
        super().__init__(keystore)

        os.makedirs(datastore_path, exist_ok=True)
        self._datastore_path = datastore_path

        if enable_db:
            db_path = f"sqlite:///{os.path.join(self._datastore_path, 'node.db')}"
            log.info('init', 'Creating default NodeDB service', path=db_path)
            self.db = DefaultNodeDBService(self, db_path)

        if dor_plugin_class is not None:
            db_path = f"sqlite:///{os.path.join(self._datastore_path, 'dor.db')}"
            plugin_name = dor_plugin_class.plugin_name()
            log.info('init', 'Creating DOR service', plugin=plugin_name, path=db_path)
            self.dor = dor_plugin_class(self, db_path)

        if rti_plugin_class is not None:
            db_path = f"sqlite:///{os.path.join(self._datastore_path, 'rti.db')}"
            plugin_name = rti_plugin_class.plugin_name()
            log.info('init', 'Creating RTI service', plugin=plugin_name, path=db_path)
            self.rti = rti_plugin_class(self, db_path,
                                        retain_job_history=retain_job_history,
                                        strict_deployment=strict_deployment)


    @property
    def datastore(self) -> str:
        return self._datastore_path

    @classmethod
    def create(
            cls, keystore: Keystore, storage_path: str, p2p_address: str, rest_address: (str, int) = None,
            bind_all_address: bool = False, enable_db: bool = True,
            dor_plugin_class: Optional[Type] = None, rti_plugin_class: Optional[Type] = None,
            retain_job_history: bool = False, strict_deployment: bool = True
    ) -> Node:
        """
        Create and start a node (sync convenience method).

        To join a network after creation, call `await node.join_network(boot_node_address)`.
        """
        node = DefaultNode(keystore, storage_path, enable_db=enable_db,
                           dor_plugin_class=dor_plugin_class, rti_plugin_class=rti_plugin_class,
                           retain_job_history=retain_job_history, strict_deployment=strict_deployment)
        run_coro_safely(node.startup(p2p_address, rest_address, bind_all_address))

        return node
