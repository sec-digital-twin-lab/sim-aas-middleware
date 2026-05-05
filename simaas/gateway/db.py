import secrets
from typing import Optional, Tuple, List, Dict
from uuid import UUID, uuid4

import sqlalchemy
import sqlalchemy_json
from fastapi import HTTPException, Header
from pydantic import BaseModel
from simaas.core.identity import Identity
from simaas.core.keystore import Keystore
from simaas.core.schemas import KeystoreContent
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()


class UserRecord(Base):
    __tablename__ = 'gateway_user'
    uuid = Column(sqlalchemy.UUID, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True)
    hashed_password = Column(String(64), nullable=False)
    failed_login_attempts = Column(Integer, nullable=False)
    enabled = Column(Boolean, nullable=False)
    keystore_content = Column(sqlalchemy_json.NestedMutableJson, nullable=False)


class APIKeyRecord(Base):
    __tablename__ = 'gateway_api_key'
    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, nullable=False, unique=True)
    uuid = Column(sqlalchemy.UUID, nullable=False)
    description = Column(String, nullable=False)


class User(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    uuid: UUID
    name: str
    email: str
    hashed_password: str
    failed_login_attempts: int
    enabled: bool
    keystore: Optional[Keystore]

    @property
    def identity(self) -> Identity:
        return self.keystore.identity


class APIKey(BaseModel):
    id: int
    key: str
    uuid: UUID
    description: str


class DatabaseWrapper:
    _engine = None
    _session_maker = None

    @classmethod
    def initialise(cls, db_url: str):
        cls._engine = create_engine(db_url)
        Base.metadata.create_all(cls._engine)
        cls._session_maker = sessionmaker(bind=cls._engine)

    @classmethod
    def _deserialise_user(cls, record, include_keystore: bool = True) -> User:
        keystore = None
        if include_keystore:
            keystore = Keystore.from_content(KeystoreContent.model_validate(record.keystore_content))
        return User(
            uuid=record.uuid, name=record.name, email=record.email,
            hashed_password=record.hashed_password,
            failed_login_attempts=record.failed_login_attempts,
            enabled=record.enabled, keystore=keystore
        )

    @classmethod
    def get_user(cls, uuid: UUID) -> Optional[User]:
        with cls._session_maker() as session:
            record = session.query(UserRecord).filter(UserRecord.uuid == uuid).first()
            return cls._deserialise_user(record) if record else None

    @classmethod
    def get_users(cls, uuids: List[UUID] = None, include_keystore: bool = False) \
            -> Tuple[Dict[UUID, User], List[UUID]]:
        with cls._session_maker() as session:
            if uuids is not None and len(uuids) > 0:
                records = session.query(UserRecord).filter(UserRecord.uuid.in_(uuids)).all()
            else:
                uuids = []
                records = session.query(UserRecord).all()

            found = {}
            for record in records:
                found[record.uuid] = cls._deserialise_user(record, include_keystore=include_keystore)

            missing = [uuid for uuid in uuids if uuid not in found]
            return found, missing

    @classmethod
    def delete_users(cls, uuids: List[UUID]) -> None:
        with cls._session_maker() as session:
            session.query(UserRecord).filter(UserRecord.uuid.in_(uuids)).delete()
            session.commit()

    @classmethod
    def update_user_status(cls, uuids: List[UUID], enabled: bool) -> None:
        with cls._session_maker() as session:
            records = session.query(UserRecord).filter(UserRecord.uuid.in_(uuids)).all()
            for record in records:
                record.enabled = enabled
            session.commit()

    @classmethod
    def create_user(cls, name: str, email: str, hashed_password: str) -> User:
        with cls._session_maker() as session:
            record = session.query(UserRecord).filter(UserRecord.email == email).first()
            if record is not None:
                raise RuntimeError(f"Login '{email}' already exists.")

            def generate_uuid() -> UUID:
                while True:
                    candidate = uuid4()
                    existing = session.query(UserRecord).filter(UserRecord.uuid == candidate).first()
                    if existing is None:
                        return candidate

            uuid = generate_uuid()
            keystore = Keystore.new(name, email)

            record = UserRecord(
                uuid=uuid, name=name, email=email, hashed_password=hashed_password,
                failed_login_attempts=0, enabled=True,
                keystore_content=keystore.content.model_dump()
            )
            session.add(record)
            session.commit()

        return cls.get_user(uuid)

    @classmethod
    def generate_key(cls, user: User, description: str) -> APIKey:
        with cls._session_maker() as session:
            key = user.uuid.hex + secrets.token_hex(16)
            record = APIKeyRecord(key=key, uuid=user.uuid, description=description)
            session.add(record)
            session.commit()
            return APIKey(id=record.id, key=key, uuid=user.uuid, description=description)

    @classmethod
    def get_keys_by_user(cls, uuids: List[UUID] = None) -> Dict[UUID, List[APIKey]]:
        with cls._session_maker() as session:
            if uuids is not None and len(uuids) > 0:
                records = session.query(APIKeyRecord).filter(APIKeyRecord.uuid.in_(uuids)).all()
            else:
                records = session.query(APIKeyRecord).all()

            result = {}
            for record in records:
                key = APIKey(id=record.id, key=record.key, uuid=record.uuid, description=record.description)
                if key.uuid in result:
                    result[key.uuid].append(key)
                else:
                    result[key.uuid] = [key]

            return result

    @classmethod
    def get_keys_by_id(cls, ids: List[int] = None) -> Tuple[Dict[int, APIKey], List[int]]:
        with cls._session_maker() as session:
            if ids is not None and len(ids) > 0:
                records = session.query(APIKeyRecord).filter(APIKeyRecord.id.in_(ids)).all()
            else:
                ids = []
                records = session.query(APIKeyRecord).all()

            found = {}
            for record in records:
                found[record.id] = APIKey(
                    id=record.id, key=record.key, uuid=record.uuid, description=record.description
                )

            missing = [key_id for key_id in ids if key_id not in found]
            return found, missing

    @classmethod
    def delete_keys(cls, key_ids: List[int]) -> None:
        with cls._session_maker() as session:
            session.query(APIKeyRecord).filter(APIKeyRecord.id.in_(key_ids)).delete()
            session.commit()

    @classmethod
    def verify_key(cls, api_key: str = Header(..., alias="Authorization")) -> Tuple[str, User]:
        if not api_key.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Invalid authorization header format")
        api_key = api_key[len("Bearer "):]

        with cls._session_maker() as session:
            record = session.query(APIKeyRecord).filter(APIKeyRecord.key == api_key).first()
            if record is None:
                raise HTTPException(status_code=401, detail="Invalid API key")

            user = cls.get_user(record.uuid)
            if user is None:
                raise HTTPException(status_code=401, detail="User not found for API key")

            if not user.enabled:
                raise HTTPException(status_code=403, detail="Account disabled")

            return api_key, user
