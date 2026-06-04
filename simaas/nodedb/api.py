from __future__ import annotations

import abc
from typing import Optional, List, Dict, Tuple

from simaas.core.identity import Identity
from simaas.core.keystore import Keystore
from simaas.decorators import public_access, requires_authentication
from simaas.nodedb.schemas import NodeInfo, NamespaceInfo, ResourceDescriptor
from simaas.rest.proxy import EndpointProxy, Session, get_proxy_prefix
from simaas.rest.schemas import EndpointDefinition

DB_ENDPOINT_PREFIX = "/api/v1/db"


class NodeDBService(abc.ABC):
    def get_p2p_protocols(self, node) -> list:
        """P2P protocols this service exposes on the node's P2P bus.

        Called once by ``Node.startup()`` before the P2P service is started.
        Subclasses (including out-of-tree plugins) extend the list by overriding
        and chaining via ``super().get_p2p_protocols(node)``.
        """
        from simaas.nodedb.protocol import (
            P2PUpdateIdentity, P2PJoinNetwork, P2PLeaveNetwork,
            P2PGetIdentity, P2PGetNetwork,
            P2PReserveNamespaceResources, P2PCancelNamespaceReservation,
            P2PUpdateNamespaceBudget,
        )
        from simaas.namespace.protocol import P2PNamespaceServiceCall
        return [
            P2PUpdateIdentity(node),
            P2PJoinNetwork(node),
            P2PLeaveNetwork(node),
            P2PGetIdentity(node),
            P2PGetNetwork(node),
            P2PReserveNamespaceResources(node),
            P2PCancelNamespaceReservation(node),
            P2PUpdateNamespaceBudget(node),
            P2PNamespaceServiceCall(node),
        ]

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
    @public_access
    def get_node(self) -> NodeInfo:
        """
        Retrieves information about the node.
        """

    @abc.abstractmethod
    @public_access
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
    @public_access
    def get_identity(self, iid: str, raise_if_unknown: bool = False) -> Optional[Identity]:
        """
        Retrieves the identity given its id (if the node db knows about it).
        """

    @abc.abstractmethod
    @public_access
    def get_identities(self) -> List[Identity]:
        """
        Retrieves a list of all identities known to the node.
        """

    @abc.abstractmethod
    @public_access
    # No header-level auth needed: the identity record is self-authenticating —
    # update_identity (see nodedb/default.py) calls Identity.verify_integrity()
    # which checks the record's own signature against its embedded public key,
    # and a monotonic nonce blocks rollback. This is the web3 self-signed
    # registration pattern.
    def update_identity(self, identity: Identity) -> Identity:
        """
        Updates an existing identity or adds a new one in case an identity with the id does not exist yet.
        """

    @abc.abstractmethod
    def delete_identity(self, iid: str) -> None:
        """
        Deletes an identity from the database if it exists.
        """

    @abc.abstractmethod
    @public_access
    def get_namespace(self, name: str) -> Optional[NamespaceInfo]:
        """
        Returns information of a specific namespace.
        """

    @abc.abstractmethod
    @public_access
    def get_namespaces(self) -> List[NamespaceInfo]:
        """
        Returns a list of all namespaces.
        """

    @abc.abstractmethod
    @requires_authentication
    # TODO(security): namespaces have no owner field today, so any known identity
    # can rewrite any namespace's budget. Decide the ownership model (node-admin
    # only, or first-caller-owns) and tighten this check. P2PUpdateNamespaceBudget
    # in nodedb/protocol.py needs the same rule.
    def update_namespace_budget(self, name: str, budget: ResourceDescriptor) -> NamespaceInfo:
        """
        Updates the resource budget for an existing namespace. If the namespace doesn't exist yet, it will be created.
        """

    @abc.abstractmethod
    def reserve_namespace_resources(self, name: str, job_id: str, resources: ResourceDescriptor) -> None:
        """
        Attempts to reserve namespace resources for a job.
        """

    @abc.abstractmethod
    def cancel_namespace_reservation(self, name: str, job_id: str) -> bool:
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

    def update_namespace_budget(self, name: str, budget: ResourceDescriptor,
                                with_authorisation_by: Keystore) -> NamespaceInfo:
        serialised_namespace = self.post(f'namespace/{name}', body=budget.model_dump(),
                                         with_authorisation_by=with_authorisation_by)
        return NamespaceInfo.model_validate(serialised_namespace) if serialised_namespace else None
