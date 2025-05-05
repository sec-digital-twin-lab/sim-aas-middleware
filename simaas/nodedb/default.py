import threading
from typing import Optional, List

from sqlalchemy import Column, String, BigInteger, Integer, Boolean
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy_json import NestedMutableJson

from simaas.core.helpers import get_timestamp_now
from simaas.core.identity import Identity
from simaas.core.logging import Logging
from simaas.nodedb.api import NodeDBService
from simaas.nodedb.exceptions import InvalidIdentityError, IdentityNotFoundError, NamespaceNotFoundError, \
    ReservationNotFoundError, ClaimNotFoundError
from simaas.nodedb.protocol import NodeDBSnapshot
from simaas.nodedb.schemas import NodeInfo, NamespaceInfo, ResourceDescriptor

logger = Logging.get('nodedb.service')

Base = declarative_base()


class NodeRecord(Base):
    __tablename__ = 'node'
    iid = Column(String(64), primary_key=True)
    last_seen = Column(BigInteger, nullable=False)
    dor_service = Column(Boolean, nullable=False)
    rti_service = Column(Boolean, nullable=False)
    p2p_address = Column(String, nullable=False)
    rest_address = Column(String, nullable=True)
    retain_job_history = Column(Boolean, nullable=True)
    strict_deployment = Column(Boolean, nullable=True)


class IdentityRecord(Base):
    __tablename__ = 'identity'
    iid = Column(String(64), primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    s_public_key = Column(String, nullable=True)
    e_public_key = Column(String, nullable=True)
    c_public_key = Column(String, nullable=True)
    nonce = Column(Integer, nullable=False)
    signature = Column(String, nullable=True)
    last_seen = Column(BigInteger, nullable=False)


class NamespaceRecord(Base):
    __tablename__ = 'namespace'
    name = Column(String(64), primary_key=True)
    budget = Column(NestedMutableJson, nullable=False)
    reservations = Column(NestedMutableJson, nullable=False)
    claims = Column(NestedMutableJson, nullable=False)
    jobs = Column(NestedMutableJson, nullable=False)


class DefaultNodeDBService(NodeDBService):
    def __init__(self, node, db_path: str):
        # initialise properties
        self._node = node
        self._mutex = threading.Lock()

        # initialise database things
        self._engine = create_engine(db_path)
        Base.metadata.create_all(self._engine)
        self._Session = sessionmaker(bind=self._engine)

    def get_node(self) -> NodeInfo:
        """
        Retrieves information about the node.
        """
        with self._Session() as session:
            record = session.query(NodeRecord).get(self._node.identity.id)
            return NodeInfo(
                identity=self._node.identity,
                last_seen=record.last_seen,
                dor_service=record.dor_service,
                rti_service=record.rti_service,
                p2p_address=record.p2p_address,
                rest_address=record.rest_address.split(':') if record.rest_address else None,
                retain_job_history=record.retain_job_history if record.retain_job_history is not None else None,
                strict_deployment=record.strict_deployment if record.strict_deployment is not None else None
            )

    def get_network(self) -> List[NodeInfo]:
        """
        Retrieves information about all peers known to the node.
        """
        with self._Session() as session:
            return [NodeInfo(
                identity=self.get_identity(record.iid, raise_if_unknown=True),
                last_seen=record.last_seen,
                dor_service=record.dor_service,
                rti_service=record.rti_service,
                p2p_address=record.p2p_address,
                rest_address=record.rest_address.split(':') if record.rest_address else None,
                retain_job_history=record.retain_job_history if record.retain_job_history is not None else None,
                strict_deployment=record.strict_deployment if record.strict_deployment is not None else None
            ) for record in session.query(NodeRecord).all()]

    def update_network(self, node: NodeInfo) -> None:
        """
        Adds information about a node to the db. If there is already information about this node in the database, the
        db is updated accordingly.
        """
        with self._Session() as session:
            # find all conflicting records, i.e., records of a node with a different iid but on the same P2P/REST
            # address but different (if any).
            p2p_address = node.p2p_address
            rest_address = f"{node.rest_address[0]}:{node.rest_address[1]}" if node.rest_address else None

            conflicting_records = session.query(NodeRecord).filter(
                (NodeRecord.iid != node.identity.id) & (
                    (NodeRecord.p2p_address == p2p_address) |
                    (NodeRecord.rest_address == rest_address if rest_address else False)
                )
            ).all()

            for record in conflicting_records:
                if record.last_seen >= node.last_seen:
                    logger.debug(f"ignoring network node update -> record with conflicting address but more recent "
                                 f"timestamp found: "
                                 f"\nrecord.iid={record.iid} <> {node.identity.id}"
                                 f"\nrecord.last_seen={record.last_seen} >= {node.last_seen}"
                                 f"\nrecord.p2p_address={record.p2p_address} <> {p2p_address}"
                                 f"\nrecord.rest_address={record.rest_address} <> {rest_address}")
                else:
                    logger.debug(f"deleting record with outdated and conflicting address: "
                                 f"\nrecord.iid={record.iid} <> {node.identity.id}"
                                 f"\nrecord.last_seen={record.last_seen} < {node.last_seen}"
                                 f"\nrecord.p2p_address={record.p2p_address} <> {p2p_address}"
                                 f"\nrecord.rest_address={record.rest_address} <> {rest_address}")

                    session.query(NodeRecord).filter_by(iid=record.iid).delete()
                    session.commit()

            # do we already have a record for this node? only update if either the record does not exist yet OR if
            # the information provided is more recent.
            record = session.query(NodeRecord).filter_by(iid=node.identity.id).first()
            if record is None:
                session.add(NodeRecord(iid=node.identity.id, last_seen=node.last_seen,
                                       dor_service=node.dor_service, rti_service=node.rti_service,
                                       p2p_address=p2p_address, rest_address=rest_address,
                                       retain_job_history=node.retain_job_history,
                                       strict_deployment=node.strict_deployment))
                session.commit()

            elif node.last_seen > record.last_seen:
                record.last_seen = node.last_seen
                record.dor_service = node.dor_service
                record.rti_service = node.rti_service
                record.p2p_address = p2p_address
                record.rest_address = rest_address
                record.retain_job_history = node.retain_job_history
                record.strict_deployment = node.strict_deployment
                session.commit()

            else:
                logger.debug(f"ignoring network node update -> more recent record found: "
                             f"\nrecord.iid={record.iid} <> {node.identity.id}"
                             f"\nrecord.last_seen={record.last_seen} >= {node.last_seen}"
                             f"\nrecord.p2p_address={record.p2p_address} <> {p2p_address}"
                             f"\nrecord.rest_address={record.rest_address} <> {rest_address}")

    def remove_node_by_id(self, identity: Identity) -> None:
        """
        Removes a node from the db, given its identity.
        """
        with self._Session() as session:
            session.query(NodeRecord).filter_by(iid=identity.id).delete()
            session.commit()

    def remove_node_by_address(self, address: (str, int)) -> None:
        """
        Removes a node from the db, given its address (host, port).
        """
        with self._Session() as session:
            session.query(NodeRecord).filter_by(p2p_address=f"{address[0]}:{address[1]}").delete()
            session.commit()

    def reset_network(self) -> None:
        """
        Resets the db, i.e., removes the information of all nodes in the db.
        """
        with self._Session() as session:
            session.query(NodeRecord).filter(NodeRecord.iid != self._node.identity.id).delete()
            session.commit()

    def get_identity(self, iid: str, raise_if_unknown: bool = False) -> Optional[Identity]:
        """
        Retrieves the identity given its id (if the node db knows about it).
        """
        with self._Session() as session:
            record = session.query(IdentityRecord).filter_by(iid=iid).first()

            if raise_if_unknown and record is None:
                raise IdentityNotFoundError(iid)

            return Identity(
                id=record.iid,
                name=record.name,
                email=record.email,
                s_public_key=record.s_public_key,
                e_public_key=record.e_public_key,
                c_public_key=record.c_public_key,
                nonce=record.nonce,
                signature=record.signature,
                last_seen=record.last_seen
            ) if record else None

    def get_identities(self) -> List[Identity]:
        """
        Retrieves a list of all identities known to the node.
        """
        with self._Session() as session:
            records = session.query(IdentityRecord).all()
            return [
                Identity(
                    id=record.iid,
                    name=record.name,
                    email=record.email,
                    s_public_key=record.s_public_key,
                    e_public_key=record.e_public_key,
                    c_public_key=record.c_public_key,
                    nonce=record.nonce,
                    signature=record.signature,
                    last_seen=record.last_seen
                ) for record in records
            ]

    def update_identity(self, identity: Identity) -> Identity:
        """
        Updates an existing identity or adds a new one in case an identity with the id does not exist yet.
        """
        # verify the integrity of the identity
        if not identity.verify_integrity():
            raise InvalidIdentityError({
                'identity': identity
            })

        # update the db
        with self._Session() as session:
            # do we have the identity already on record?
            record = session.query(IdentityRecord).filter_by(iid=identity.id).first()
            if record is None:
                session.add(IdentityRecord(iid=identity.id, name=identity.name, email=identity.email,
                                           s_public_key=identity.s_public_key, e_public_key=identity.e_public_key,
                                           c_public_key=identity.c_public_key, nonce=identity.nonce,
                                           signature=identity.signature, last_seen=get_timestamp_now()))
                session.commit()

            # only perform update if either the record does not exist yet OR if the information provided is valid
            # and more recent, i.e., if the nonce is greater than the one on record.
            elif identity.nonce > record.nonce:
                record.name = identity.name
                record.email = identity.email
                record.nonce = identity.nonce
                record.s_key = identity.s_public_key
                record.e_key = identity.e_public_key
                record.c_key = identity.c_public_key
                record.signature = identity.signature
                record.last_seen = get_timestamp_now()
                session.commit()

            else:
                logger.debug("Ignore identity update as nonce on record is more recent.")

        return self.get_identity(identity.id, raise_if_unknown=True)

    def get_snapshot(self, exclude: List[str] = None) -> NodeDBSnapshot:
        """
        Retrieves a snapshot of the contents stored in the db.
        """
        # get all nodes we know of (minus the ones to exclude)
        nodes = []
        for node in self.get_network():
            if not exclude or node.identity.id not in exclude:
                nodes.append(node)

        # get all identities we know of (minus the ones to exclude)
        identities = []
        for identity in self.get_identities():
            if not exclude or identity.id not in exclude:
                identities.append(identity)

        # get all namespaces we know of
        namespaces = self.get_namespaces()

        return NodeDBSnapshot(update_identity=identities, update_network=nodes, update_namespace=namespaces)

    def touch_identity(self, identity: Identity) -> None:
        with self._Session() as session:
            # do we have the identity already on record?
            record = session.query(IdentityRecord).get(identity.id)
            if record is None:
                raise IdentityNotFoundError(identity.id)

            record.last_accessed = get_timestamp_now()
            session.commit()

    def get_namespace(self, name: str) -> Optional[NamespaceInfo]:
        """
        Returns information about a namespace (if it exists).
        """
        with self._Session() as session:
            record = session.query(NamespaceRecord).get(name)
            return NamespaceInfo(
                name=record.name,
                budget=ResourceDescriptor.model_validate(record.budget),
                reservations={k: ResourceDescriptor.model_validate(v) for k, v in record.reservations.items()},
                claims={k: ResourceDescriptor.model_validate(v) for k, v in record.claims.items()},
                jobs=[job_id for job_id in record.jobs]
            ) if record else None

    def get_namespaces(self) -> List[NamespaceInfo]:
        """
        Returns a list of all namespaces.
        """
        with self._Session() as session:
            records = session.query(NamespaceRecord).all()
            return [
                NamespaceInfo(
                    name=record.name,
                    budget=ResourceDescriptor.model_validate(record.budget),
                    reservations={k: ResourceDescriptor.model_validate(v) for k, v in record.reservations.items()},
                    claims={k: ResourceDescriptor.model_validate(v) for k, v in record.claims.items()},
                    jobs=[job_id for job_id in record.jobs]
                ) for record in records
            ]

    def update_namespace_budget(self, name: str, budget: ResourceDescriptor) -> NamespaceInfo:
        """
        Updates the resource budget for an existing namespace. If the namespace doesn't exist yet, it will be created.
        """
        with self._mutex:
            with self._Session() as session:
                record = session.query(NamespaceRecord).get(name)
                if record is None:
                    record = NamespaceRecord(
                        name=name,
                        budget=budget.model_dump(),
                        reservations={},
                        claims={},
                        jobs=[]
                    )
                    session.add(record)
                    session.commit()
                else:
                    record.budget=budget.model_dump()
                    session.commit()

                return NamespaceInfo(
                    name=record.name,
                    budget=ResourceDescriptor.model_validate(record.budget),
                    reservations={k: ResourceDescriptor.model_validate(v) for k, v in record.reservations.items()},
                    claims={k: ResourceDescriptor.model_validate(v) for k, v in record.claims.items()},
                    jobs=[job_id for job_id in record.jobs]
                )

    def namespace_handle_reservation(self, namespace: str, request: ResourceDescriptor, reservation_id: str) -> bool:
        with self._mutex:
            with self._Session() as session:
                # does the namespace exist?
                record: Optional[NamespaceRecord] = session.query(NamespaceRecord).get(namespace)
                if record is None:
                    raise NamespaceNotFoundError(namespace)

                # determine the budget
                budget: ResourceDescriptor = ResourceDescriptor.model_validate(record.budget)
                vcpus_available = budget.vcpus
                memory_available = budget.memory

                # consider all reservations
                for _, reservation in record.reservations.items():
                    reservation: ResourceDescriptor = reservation
                    vcpus_available -= reservation.vcpus
                    memory_available -= reservation.memory

                # consider all current claims
                for job_id, claim in record.claims.items():
                    claim: ResourceDescriptor = claim

                    vcpus_available -= claim.vcpus
                    memory_available -= claim.memory

                # does the namespace has enough resources left?
                sufficient_vcpus = request.vcpus <= vcpus_available
                sufficient_memory = request.memory <= memory_available
                if sufficient_vcpus and sufficient_memory:
                    # add the reservation
                    record.reservations[reservation_id] = request.model_dump()
                    session.commit()
                    return True

                else:
                    return False

    def namespace_cancel_reservation(self, namespace: str, reservation_id: str) -> None:
        with self._mutex:
            with self._Session() as session:
                # does the namespace exist?
                record: Optional[NamespaceRecord] = session.query(NamespaceRecord).get(namespace)
                if record is not None:
                    # do we have this reservation?
                    reservations = dict(record.reservations)
                    if reservation_id in reservations:
                        reservations.pop(reservation_id)
                        record.reservations = reservations
                        session.commit()

    def namespace_handle_claim(self, namespace: str, reservation_id: str, job_id: str) -> None:
        with self._mutex:
            with self._Session() as session:
                # does the namespace exist?
                record: Optional[NamespaceRecord] = session.query(NamespaceRecord).get(namespace)
                if record is None:
                    raise NamespaceNotFoundError(namespace)

                # do we have this reservation?
                reservations = dict(record.reservations)
                if reservation_id not in reservations:
                    raise ReservationNotFoundError(namespace, reservation_id)

                # remove the reservation
                resources: ResourceDescriptor = reservations.pop(reservation_id)
                record.reservations = reservations

                # add to the claims and also add the job id to the jobs
                claims = dict(record.claims)
                claims[job_id] = resources
                record.claims = claims
                record.jobs.append(job_id)
                session.commit()

    def namespace_handle_release(self, namespace: str, job_id: str) -> None:
        with self._mutex:
            with self._Session() as session:
                # does the namespace exist?
                record: Optional[NamespaceRecord] = session.query(NamespaceRecord).get(namespace)
                if record is None:
                    raise NamespaceNotFoundError(namespace)

                # do we have the claim
                claims = dict(record.claims)
                if job_id not in claims:
                    raise ClaimNotFoundError(namespace, job_id)

                # remove the claim
                claims.pop(job_id)
                record.claims = claims
                session.commit()

    def update_namespace(self, namespace: NamespaceInfo) -> None:
        with self._Session() as session:
            record = session.query(NamespaceRecord).get(namespace.name)
            if record is None:
                record = NamespaceRecord(
                    name=namespace.name,
                    budget=namespace.budget.model_dump(),
                    reservations={k: v.model_dump() for k, v in namespace.reservations.items()},
                    claims={k: v.model_dump() for k, v in namespace.claims.items()},
                    jobs=namespace.jobs
                )
                session.add(record)
                session.commit()
            else:
                record.budget = namespace.budget.model_dump(),
                record.reservations = {k: v.model_dump() for k, v in namespace.reservations.items()},
                record.claims = {k: v.model_dump() for k, v in namespace.claims.items()},
                record.jobs = namespace.jobs
                session.commit()

