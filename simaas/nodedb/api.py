from __future__ import annotations

import abc
from typing import Optional, List, Dict, Tuple

from simaas.core.identity import Identity
from simaas.nodedb.schemas import NodeInfo, NamespaceInfo, ResourceDescriptor
from simaas.rest.proxy import EndpointProxy, Session, get_proxy_prefix
from simaas.rest.schemas import EndpointDefinition

DB_ENDPOINT_PREFIX = "/api/v1/db"


class NodeDBService(abc.ABC):
    def endpoints(self) -> List[EndpointDefinition]:
        return [
            EndpointDefinition('GET', DB_ENDPOINT_PREFIX, 'node',
                               self.get_node, NodeInfo),

            EndpointDefinition('GET', DB_ENDPOINT_PREFIX, 'network',
                               self.get_network, List[NodeInfo]),

            EndpointDefinition('GET', DB_ENDPOINT_PREFIX, 'identity/{iid}',
                               self.get_identity, Optional[Identity]),

            EndpointDefinition('GET', DB_ENDPOINT_PREFIX, 'identity',
                               self.get_identities, List[Identity]),

            EndpointDefinition('POST', DB_ENDPOINT_PREFIX, 'identity',
                               self.update_identity, Identity),

            EndpointDefinition('GET', DB_ENDPOINT_PREFIX, 'namespace/{name}',
                               self.get_namespace, Optional[NamespaceInfo]),

            EndpointDefinition('GET', DB_ENDPOINT_PREFIX, 'namespace',
                               self.get_namespaces, List[NamespaceInfo]),

            EndpointDefinition('POST', DB_ENDPOINT_PREFIX, 'namespace/{name}',
                               self.update_namespace_budget, NamespaceInfo),
        ]

    @abc.abstractmethod
    async def get_node(self) -> NodeInfo:
        """
        Retrieves information about the node.
        """

    @abc.abstractmethod
    async def get_network(self) -> List[NodeInfo]:
        """
        Retrieves information about all peers known to the node.
        """

    @abc.abstractmethod
    async def update_network(self, node: NodeInfo) -> None:
        """
        Adds information about a node to the db. If there is already information about this node in the database, the
        db is updated accordingly.
        """

    @abc.abstractmethod
    async def get_identity(self, iid: str, raise_if_unknown: bool = False) -> Optional[Identity]:
        """
        Retrieves the identity given its id (if the node db knows about it).
        """

    @abc.abstractmethod
    async def get_identities(self) -> List[Identity]:
        """
        Retrieves a list of all identities known to the node.
        """

    @abc.abstractmethod
    async def update_identity(self, identity: Identity) -> Identity:
        """
        Updates an existing identity or adds a new one in case an identity with the id does not exist yet.
        """

    @abc.abstractmethod
    async def delete_identity(self, iid: str) -> None:
        """
        Deletes an identity from the database if it exists.
        """

    @abc.abstractmethod
    async def get_namespace(self, name: str) -> Optional[NamespaceInfo]:
        """
        Returns information of a specific namespace.
        """

    @abc.abstractmethod
    async def get_namespaces(self) -> List[NamespaceInfo]:
        """
        Returns a list of all namespaces.
        """

    @abc.abstractmethod
    async def update_namespace_budget(self, name: str, budget: ResourceDescriptor) -> NamespaceInfo:
        """
        Updates the resource budget for an existing namespace. If the namespace doesn't exist yet, it will be created.
        """

    @abc.abstractmethod
    async def reserve_namespace_resources(self, name: str, job_id: str, resources: ResourceDescriptor) -> None:
        """
        Attempts to reserve namespace resources for a job.
        """

    @abc.abstractmethod
    async def cancel_namespace_reservation(self, name: str, job_id: str) -> bool:
        """
        Cancels a namespace resource reservation (if it exists).
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
        return NodeInfo.model_validate(result)

    def get_network(self) -> List[NodeInfo]:
        results = self.get("network")
        return [NodeInfo.model_validate(result) for result in results]

    def get_identities(self) -> Dict[str, Identity]:
        return {
            item['id']: Identity.model_validate(item) for item in self.get("identity")
        }

    def get_identity(self, iid: str) -> Optional[Identity]:
        serialised_identity = self.get(f"identity/{iid}")
        return Identity.model_validate(serialised_identity) if serialised_identity else None

    def update_identity(self, identity: Identity) -> Optional[Identity]:
        serialised_identity = self.post('identity', body=identity.model_dump())
        return Identity.model_validate(serialised_identity) if serialised_identity else None

    def get_namespace(self, name: str) -> Optional[NamespaceInfo]:
        serialised_namespace = self.get(f"namespace/{name}")
        return NamespaceInfo.model_validate(serialised_namespace) if serialised_namespace else None

    def get_namespaces(self) -> Dict[str, NamespaceInfo]:
        return {
            item['name']: NamespaceInfo.model_validate(item) for item in self.get("namespace")
        }

    def update_namespace_budget(self, name: str, budget: ResourceDescriptor) -> NamespaceInfo:
        serialised_namespace = self.post(f'namespace/{name}', body=budget.model_dump())
        return NamespaceInfo.model_validate(serialised_namespace) if serialised_namespace else None
