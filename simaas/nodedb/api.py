from __future__ import annotations

import abc
from typing import Optional, List, Dict, Tuple

from simaas.core.identity import Identity
from simaas.nodedb.schemas import NodeInfo
from simaas.rest.proxy import EndpointProxy, Session, get_proxy_prefix
from simaas.rest.schemas import EndpointDefinition

DB_ENDPOINT_PREFIX = "/api/v1/db"


class NodeDBService(abc.ABC):
    def endpoints(self) -> List[EndpointDefinition]:
        return [
            EndpointDefinition('GET', DB_ENDPOINT_PREFIX, 'node',
                               self.get_node, NodeInfo, None),

            EndpointDefinition('GET', DB_ENDPOINT_PREFIX, 'network',
                               self.get_network, List[NodeInfo], None),

            EndpointDefinition('GET', DB_ENDPOINT_PREFIX, 'identity/{iid}',
                               self.get_identity, Optional[Identity], None),

            EndpointDefinition('GET', DB_ENDPOINT_PREFIX, 'identity',
                               self.get_identities, List[Identity], None),

            EndpointDefinition('POST', DB_ENDPOINT_PREFIX, 'identity',
                               self.update_identity, Identity, None),
        ]

    @abc.abstractmethod
    def get_node(self) -> NodeInfo:
        """
        Retrieves information about the node.
        """

    @abc.abstractmethod
    def get_network(self) -> List[NodeInfo]:
        """
        Retrieves information about all peers known to the node.
        """

    @abc.abstractmethod
    def update_network(self, node: NodeInfo) -> None:
        """
        Adds information about a node to the db. If there is already information about this node in the database, the
        db is updated accordingly.
        """

    @abc.abstractmethod
    def get_identity(self, iid: str, raise_if_unknown: bool = False) -> Optional[Identity]:
        """
        Retrieves the identity given its id (if the node db knows about it).
        """

    @abc.abstractmethod
    def get_identities(self) -> List[Identity]:
        """
        Retrieves a list of all identities known to the node.
        """

    @abc.abstractmethod
    def update_identity(self, identity: Identity) -> Identity:
        """
        Updates an existing identity or adds a new one in case an identity with the id does not exist yet.
        """


class NodeDBProxy(EndpointProxy):
    @classmethod
    def from_session(cls, session: Session) -> NodeDBProxy:
        return NodeDBProxy(remote_address=session.address, credentials=session.credentials,
                           endpoint_prefix=(session.endpoint_prefix_base, 'db'))

    def __init__(self, remote_address: (str, int), credentials: (str, str) = None,
                 endpoint_prefix: Tuple[str, str] = get_proxy_prefix(DB_ENDPOINT_PREFIX)):
        super().__init__(endpoint_prefix, remote_address, credentials=credentials)

    def get_node(self) -> NodeInfo:
        result = self.get("node")
        return NodeInfo.parse_obj(result)

    def get_network(self) -> List[NodeInfo]:
        results = self.get("network")
        return [NodeInfo.parse_obj(result) for result in results]

    def get_identities(self) -> Dict[str, Identity]:
        return {
            item['id']: Identity.parse_obj(item) for item in self.get("identity")
        }

    def get_identity(self, iid: str) -> Optional[Identity]:
        serialised_identity = self.get(f"identity/{iid}")
        return Identity.parse_obj(serialised_identity) if serialised_identity else None

    def update_identity(self, identity: Identity) -> Optional[Identity]:
        serialised_identity = self.post('identity', body=identity.dict())
        return Identity.parse_obj(serialised_identity) if serialised_identity else None
