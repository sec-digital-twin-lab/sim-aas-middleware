import json
import math
import os
import traceback
from datetime import datetime, timezone
from typing import Union, Optional, Tuple

import canonicaljson
import pydantic
import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes

from simaas.core.exceptions import ExceptionContent
from simaas.core.helpers import generate_random_string
from simaas.core.keystore import Keystore
from simaas.dor.schemas import DORFilePartInfo
from simaas.rest.exceptions import UnexpectedHTTPError, UnsuccessfulRequestError, UnexpectedContentType, \
    UnsuccessfulConnectionError, AuthorisationFailedError
from simaas.rest.schemas import Token


def get_proxy_prefix(endpoint_prefix: str) -> Tuple[str, str]:
    endpoint_split = endpoint_prefix.rsplit("/", 1)
    return endpoint_split[0], endpoint_split[1]


def extract_response(response: requests.Response) -> Optional[Union[dict, list]]:
    """
    Extracts the response content in case of an 'Ok' response envelope or raises an exception in case
    of an 'Error' envelope.
    :param response: the response message
    :return: extracted response content (if any)
    :raise UnsuccessfulRequestError
    """

    if response.status_code == 200:
        return response.json()

    elif response.status_code == 500:
        try:
            # try to parse the response as JSON and then the JSON as as ExceptionContent
            content = response.json()
            content = ExceptionContent.parse_obj(content)
            exception = UnsuccessfulRequestError(content.reason, exception_id=content.id, details=content.details)

        # the content is not even JSON...
        except requests.exceptions.JSONDecodeError:
            exception = UnsuccessfulRequestError(response.reason, details={
                'status_code': 500,
            })

        # the content is JSON but not of ExceptionContent type
        except pydantic.ValidationError:
            exception = UnsuccessfulRequestError(response.reason, details={
                'content': response.json(),
                'status_code': 500,
            })

        # something unexpected went wrong
        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            exception = UnsuccessfulRequestError(response.reason, details={
                'trace': trace,
                'status_code': 500,
            })

        raise exception

    elif response.status_code == 401:
        raise AuthorisationFailedError()

    else:
        raise UnexpectedHTTPError({
            'reason': response.reason,
            'status_code': response.status_code
        })


def generate_authorisation_token(authority: Keystore, url: str, body: dict = None) -> str:
    digest = hashes.Hash(hashes.SHA256(), backend=default_backend())

    digest.update(url.encode('utf-8'))
    if body:
        digest.update(canonicaljson.encode_canonical_json(body))

    token = digest.finalize()
    return authority.sign(token)


def _make_headers(url: str, body: Union[dict, list] = None, authority: Keystore = None,
                  token: Token = None) -> dict:

    headers = {}

    if authority:
        headers['saasauth-iid'] = authority.identity.id
        headers['saasauth-signature'] = generate_authorisation_token(authority, url, body)

    if token:
        headers['Authorization'] = f"Bearer {token.access_token}"

    return headers


class Session:
    def __init__(self, endpoint_prefix_base: str, remote_address: Union[tuple[str, str, int], tuple[str, int]],
                 credentials: (str, str)) -> None:
        self._endpoint_prefix_base = endpoint_prefix_base

        self._remote_address = \
            remote_address if len(remote_address) == 3 else ('http', remote_address[0], remote_address[1])

        self._credentials = credentials

        self._token = None
        self._expiry = None

    @property
    def endpoint_prefix_base(self) -> str:
        return self._endpoint_prefix_base

    @property
    def address(self) -> (str, str, int):
        return self._remote_address

    @property
    def credentials(self) -> (str, str):
        return self._credentials

    def refresh_token(self) -> Token:
        data = {
            'grant_type': 'password',
            'username': self._credentials[0],
            'password': self._credentials[1]
        }

        # get the token
        url = f"{self._remote_address[0]}://{self._remote_address[1]}" if self._remote_address[2] is None else \
            f"{self._remote_address[0]}://{self._remote_address[1]}:{self._remote_address[2]}"
        url = f"{url}{self._endpoint_prefix_base}/token"

        try:
            response = requests.post(url, data=data)
            result = extract_response(response)
            self._token = Token.parse_obj(result)
            return self._token

        except requests.exceptions.ConnectionError:
            raise UnsuccessfulConnectionError(url)

    @property
    def token(self) -> Token:
        now = datetime.now(tz=timezone.utc).timestamp()
        if self._token is None or now > self._token.expiry - 60:
            self.refresh_token()

        return self._token


class EndpointProxy:
    def __init__(self, endpoint_prefix: (str, str), remote_address: Union[tuple[str, str, int], tuple[str, int]],
                 credentials: (str, str) = None) -> None:
        self._endpoint_prefix = endpoint_prefix
        self._remote_address = \
            remote_address if len(remote_address) == 3 else ('http', remote_address[0], remote_address[1])
        self._session = Session(endpoint_prefix[0], remote_address, credentials) if credentials else None

    @property
    def remote_address(self) -> (str, str, int):
        return self._remote_address

    @property
    def session(self) -> Session:
        return self._session

    def get(self, endpoint: str, body: Union[dict, list] = None, parameters: dict = None, download_path: str = None,
            with_authorisation_by: Keystore = None) -> Optional[Union[dict, list]]:

        url = self._make_url(endpoint, parameters)
        headers = _make_headers(f"GET:{url}", body=body, authority=with_authorisation_by,
                                token=self._session.token if self._session else None)

        try:
            if download_path:
                with requests.get(url, headers=headers, json=body, stream=True) as response:
                    header = {k.lower(): v for k, v in response.headers.items()}
                    if header['content-type'] == 'application/json':
                        return extract_response(response)

                    elif response.headers['content-type'] == 'application/octet-stream':
                        content = response.iter_content(chunk_size=8192)
                        with open(download_path, 'wb') as f:
                            for chunk in content:
                                f.write(chunk)
                        return header

                    else:
                        raise UnexpectedContentType({
                            'header': header
                        })

            else:
                response = requests.get(url, headers=headers, json=body)
                return extract_response(response)

        except requests.exceptions.ConnectionError:
            raise UnsuccessfulConnectionError(url)

    def put(self, endpoint: str, body: Union[dict, list] = None, parameters: dict = None, attachment_path: str = None,
            with_authorisation_by: Keystore = None) -> Union[dict, list]:

        url = self._make_url(endpoint, parameters)
        headers = _make_headers(f"PUT:{url}", body=body, authority=with_authorisation_by,
                                token=self._session.token if self._session else None)

        try:
            if attachment_path:
                with open(attachment_path, 'rb') as f:
                    response = requests.put(url,
                                            headers=headers,
                                            data={'body': json.dumps(body)} if body else None,
                                            files={'attachment': f}
                                            )

                    return extract_response(response)

            else:
                response = requests.put(url, headers=headers, json=body)
                return extract_response(response)

        except requests.exceptions.ConnectionError:
            raise UnsuccessfulConnectionError(url)

    def post(self, endpoint: str, body: Union[dict, list, str] = None, data=None, parameters: dict = None,
             attachment_path: str = None, with_authorisation_by: Keystore = None,
             max_part_size: int = 128*1024*1024) -> Union[dict, list]:

        url = self._make_url(endpoint, parameters)
        headers = _make_headers(f"POST:{url}", body=body, authority=with_authorisation_by,
                                token=self._session.token if self._session else None)

        try:
            if attachment_path:
                # determine the number of parts
                file_size = os.path.getsize(attachment_path)
                n_parts = int(math.ceil(file_size / max_part_size))

                with open(attachment_path, 'rb') as f:
                    rnd_id = generate_random_string(4)
                    i = 0
                    while True:
                        # read the i-th part
                        part = f.read(max_part_size)
                        if not part:
                            break

                        # write the part to disk
                        part_path = f"{attachment_path}.{i}"
                        with open(part_path, 'wb') as f_part:
                            f_part.write(part)

                        # update the body
                        part_info = DORFilePartInfo(id=rnd_id, idx=i, n=n_parts)
                        body['__part_info'] = part_info.dict()

                        # send the part
                        with open(part_path, 'rb') as f_part:
                            response = requests.post(url,
                                                     headers=headers,
                                                     data={'body': json.dumps(body)} if body else None,
                                                     files={'attachment': f_part}
                                                     )
                        # delete the part
                        os.remove(part_path)

                        i += 1

                    return extract_response(response)

            else:
                response = requests.post(url, headers=headers, data=data, json=body)
                return extract_response(response)

        except requests.exceptions.ConnectionError as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            raise UnsuccessfulConnectionError(url, details={
                'trace': trace
            })

    def delete(self, endpoint: str, body: Union[dict, list] = None, parameters: dict = None,
               with_authorisation_by: Keystore = None) -> Union[dict, list]:

        url = self._make_url(endpoint, parameters)
        headers = _make_headers(f"DELETE:{url}", body=body, authority=with_authorisation_by,
                                token=self._session.token if self._session else None)

        try:
            response = requests.delete(url, headers=headers, json=body)
            return extract_response(response)

        except requests.exceptions.ConnectionError:
            raise UnsuccessfulConnectionError(url)

    def _make_url(self, endpoint: str, parameters: dict = None) -> str:
        # create base URL
        url = f"{self._remote_address[0]}://{self._remote_address[1]}"
        if self._remote_address[2]:
            url = f"{url}:{self.remote_address[2]}"

        # add endpoint prefix
        url += f"{self._endpoint_prefix[0]}/{self._endpoint_prefix[1]}" if self._endpoint_prefix[1] \
            else self._endpoint_prefix[0]

        # add endpoint
        url += f"/{endpoint}"

        if parameters:
            eligible = {}
            for k, v in parameters.items():
                if k is not None and v is not None:
                    eligible[k] = v

            if eligible:
                url += '?' + '&'.join(f"{k}={v}" for k, v in eligible.items())

        return url
