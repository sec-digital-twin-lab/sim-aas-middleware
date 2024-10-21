from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional

from simaas.core.eckeypair import ECKeyPair
from simaas.core.rsakeypair import RSAKeyPair


def generate_identity_token(iid: str, name: str, email: str, nonce: int, s_public_key: str, e_public_key: str) -> str:
    return f"{iid}:{name}:{email}:{nonce}:{s_public_key}:{e_public_key}"


class Identity(BaseModel):
    """
    Record of public information about an identity.
    """
    id: str = Field(..., title="Id", description="The id of the identity.", example="yllv41nn9s1565b8yg2wv6jgwi2mrffq0l5d99q0096gfjraecxpx4ql51j9qvys")
    name: str = Field(..., title="Contact Name", description="The contact email address of the identity.", example="Foo Bar")
    email: str = Field(..., title="Contact E-Mail", description="The contact email address of the identity.", example="foo.bar@somewhere.com")
    s_public_key: str = Field(..., title="Public Signing Key", description="Used for signature verification.", example="MHYwEAYHKoZIzj0CAQYFK4EEACIDYgAE2U87iJMpRORLgfhLXZBcwHMyZjccOGSUHV3fZ79y7AvEBR+ey8K8s4/sf1+N+ULwRyp/K39LHDB31N7GJH56v2oZxcGo72jNnbIVJSyQnET5JxJaeviVD0ZUvo/jlZeM")
    e_public_key: str = Field(..., title="Public Encryption Key", description="Used for encryption.", example="MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEApXdoOcYEDthunuebGvmlY2GGfz0fAgyW8m3hXoHQlV6pRMpmplhQTCDjS2BA05a2YHZhunCaS1OEoaWvDzLhx6R3Erd6ZS27zok9ZKLRLswBUtWIIV17sJRCPkAqddV6Mzimd6+6wT11lXGHWLsODpx0z/nb1zAJ4mLHywiDr2k+wNMtrcp9n4vYj9wM1xjrkUx56NuTMPlQ8JVm0isNxQMLel0e1Wqcz4+7kSU8Ighf3Bm5OOKkbXO/ICNC8whib7qCv4s+o+Qn5msWfsu1I8BCFmzeuJhjbK1fvfyn9q8gqKBQEbzsIpKTC/bdyNpbCwabofgY/akx2w4Zeb5Q5S7qcCLXZ8tTuuYKyYIQ0U+FTZD08360HLDsBU5i7KUzADU5cpWeg27RgxTwkyF/4IcD9I8J13afK+DUu4Vo+M1osVvX154zFkNtSAEyNOzppQz7BKuoBxoazSqGIIUP/Tgg7rAXkrRcDGGgHDTtQOqFPoC8PbtSoD7krK66G5A2+yXgpZfbvHnvzabqKwZQixOw92k+ZWc2SG2jBATYtRaZDjbgyjSZ0w2aKRhvQiDn2cpMvwvlJD4zQD8l9R7VP9Fp39iraCZIXImSmKYJq6p4ZJw6kEPnnk9rFg0vnTRcjBt3rq3fNTpdfuPU18tMkB/rI0KZt9PfqgHOYo9qpEECAwEAAQ==")
    c_public_key: str = Field(..., title="Public Curve Key", description="Used to establish secure P2P communication channel.")
    nonce: int = Field(..., title="Nonce", description="A non-negative integer value that is increased by 1 every time the identity information is updated. The nonce is used by the system to decide if a record is more recent than another.", example=3)
    signature: Optional[str] = Field(title="Signature", description="A signature that can be used to verify that the contents provided by this identity information record has been provided by the entity that controls the identity's private key. Unless the private key has been lost/stolen, the information should be considered as legit if the signature is valid. The NodeDB rejects idenitity updates with invalid signatures. Invalid records should thus not be observed during normal operations.", example="3066023100ecfb8f728ee2ae59196e173a2f57bdf1015815917007b6dead5873d803fe0953253baa83d7fc2dc2c81817a7e078c445023100d336a870cbe888473941be3e56c8ccf55dde8f39ae086d8ce673296e4a6692fb1ca8bcc279d0d1734889592829c77fc7")
    last_seen: Optional[int] = Field(title="Last Seen", description="The timestamp (in UTC milliseconds since the beginning of the epoch) when the identity has been seen last.", example=1664849510076)

    def verify(self, message: bytes, signature: str) -> bool:
        s_key = ECKeyPair.from_public_key_string(self.s_public_key)
        return s_key.verify(message, signature)

    def encrypt(self, content: bytes) -> bytes:
        e_key = RSAKeyPair.from_public_key_string(self.e_public_key)
        return e_key.encrypt(content, base64_encoded=True)

    def verify_integrity(self) -> bool:
        token = generate_identity_token(self.id, self.name, self.email, self.nonce,
                                        self.s_public_key, self.e_public_key)
        return self.verify(token.encode('utf-8'), self.signature)
