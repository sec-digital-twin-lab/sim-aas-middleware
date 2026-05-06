import json
import httpx

from typing import List, Tuple, Dict, Optional
from fastapi import FastAPI, Depends, Form, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from simaas.core.errors import _BaseError
from simaas.dor.api import DORProxy, DOR_ENDPOINT_PREFIX
from simaas.dor.schemas import DataObject, GitProcessorPointer
from simaas.nodedb.api import NodeDBProxy
from simaas.nodedb.schemas import ResourceDescriptor
from simaas.rest.proxy import generate_authorisation_token
from simaas.rti.api import RTIProxy
from simaas.rti.schemas import Job, JobStatus, Processor, Task

from simaas.gateway.db import DatabaseWrapper, User


class Proxies:
    def __init__(self):
        self._address = None
        self._default_target_node_iid = None

    @property
    def address(self) -> Tuple[str, int]:
        return self._address

    @address.setter
    def address(self, address: Tuple[str, int]) -> None:
        self._address = address
        self._default_target_node_iid = None

    @property
    def default_target_node_iid(self) -> str:
        if self._default_target_node_iid is None:
            available_dor_nodes = [node for node in self.nodedb.get_network() if node.dor_service]
            self._default_target_node_iid = available_dor_nodes[0].identity.id
        return self._default_target_node_iid

    @property
    def dor(self) -> DORProxy:
        return DORProxy(self._address)

    @property
    def rti(self) -> RTIProxy:
        return RTIProxy(self._address)

    @property
    def nodedb(self) -> NodeDBProxy:
        return NodeDBProxy(self._address)


class SubmitJobRequest(BaseModel):
    task_input: List[dict]
    task_output: List[dict]
    name: Optional[str] = None
    description: Optional[str] = None
    budget: Optional[Dict[str, int]] = None
    output_access_restricted: bool = True
    output_content_encrypted: bool = False


app = FastAPI(title="SaaS Gateway API")
proxies = Proxies()


def _handle_error(e: Exception):
    if isinstance(e, _BaseError):
        raise HTTPException(status_code=500, detail=f"[{e.id}] {e.reason}")
    if isinstance(e, HTTPException):
        raise e
    raise HTTPException(status_code=500, detail=str(e))


# --- Data Endpoints ---

@app.get("/gateway/v1/data")
async def search_data_objects(
    patterns: str = None, data_type: str = None, data_format: str = None, c_hashes: str = None,
    credentials: Tuple[str, User] = Depends(DatabaseWrapper.verify_key)
) -> List[DataObject]:
    try:
        owner_iid = credentials[1].keystore.identity.id
        return proxies.dor.search(
            patterns=patterns.split(',') if patterns else None,
            owner_iid=owner_iid,
            data_type=data_type,
            data_format=data_format,
            c_hashes=c_hashes.split(',') if c_hashes else None
        )
    except Exception as e:
        _handle_error(e)


@app.post("/gateway/v1/data")
async def upload_data_object(
    body: str = Form(...), attachment: UploadFile = File(...),
    credentials: Tuple[str, User] = Depends(DatabaseWrapper.verify_key)
) -> DataObject:
    try:
        url = f"http://{proxies.address[0]}:{proxies.address[1]}{DOR_ENDPOINT_PREFIX}/add"
        keystore = credentials[1].keystore
        headers = {
            'saasauth-iid': keystore.identity.id,
            'saasauth-signature': generate_authorisation_token(keystore, f"POST:{url}", None)
        }

        parsed = json.loads(body)
        dor_body = {
            'owner_iid': keystore.identity.id,
            'creators_iid': [keystore.identity.id],
            'data_type': parsed['data_type'],
            'data_format': parsed['data_format'],
            'access_restricted': parsed.get('access_restricted', True),
            'content_encrypted': parsed.get('content_encrypted', False),
            'license': {'by': False, 'sa': False, 'nc': False, 'nd': False},
            'recipe': parsed.get('recipe'),
            'tags': parsed.get('tags')
        }

        async with httpx.AsyncClient() as client:
            form_data = {
                'body': (None, json.dumps(dor_body), 'application/json'),
                'attachment': (attachment.filename, await attachment.read(), attachment.content_type)
            }
            response = await client.post(url, headers=headers, files=form_data)
            response.raise_for_status()
            return DataObject.model_validate(response.json())

    except Exception as e:
        _handle_error(e)


@app.delete("/gateway/v1/data/{obj_id}")
async def delete_data_object(
    obj_id: str, credentials: Tuple[str, User] = Depends(DatabaseWrapper.verify_key)
) -> DataObject:
    try:
        return proxies.dor.delete_data_object(obj_id, with_authorisation_by=credentials[1].keystore)
    except Exception as e:
        _handle_error(e)


@app.get("/gateway/v1/data/{obj_id}/meta")
async def get_data_object_meta(
    obj_id: str, _: Tuple[str, User] = Depends(DatabaseWrapper.verify_key)
) -> DataObject:
    try:
        obj = proxies.dor.get_meta(obj_id)
        if obj is None:
            raise HTTPException(status_code=404, detail=f"Data object '{obj_id}' not found")
        return obj
    except Exception as e:
        _handle_error(e)


@app.get("/gateway/v1/data/{obj_id}/content")
async def download_data_object_content(
    obj_id: str, credentials: Tuple[str, User] = Depends(DatabaseWrapper.verify_key)
):
    try:
        url = f"http://{proxies.address[0]}:{proxies.address[1]}{DOR_ENDPOINT_PREFIX}/{obj_id}/content"
        keystore = credentials[1].keystore
        headers = {
            'saasauth-iid': keystore.identity.id,
            'saasauth-signature': generate_authorisation_token(keystore, f"GET:{url}", None)
        }

        async def forward_stream():
            async with httpx.AsyncClient() as client:
                async with client.stream("GET", url, headers=headers) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                        yield chunk

        return StreamingResponse(forward_stream(), media_type="application/octet-stream")

    except Exception as e:
        _handle_error(e)


@app.put("/gateway/v1/data/{obj_id}/tags")
async def update_data_object_tags(
    obj_id: str, tags: List[DataObject.Tag],
    credentials: Tuple[str, User] = Depends(DatabaseWrapper.verify_key)
) -> DataObject:
    try:
        return proxies.dor.update_tags(obj_id, authority=credentials[1].keystore, tags=tags)
    except Exception as e:
        _handle_error(e)


@app.delete("/gateway/v1/data/{obj_id}/tags")
async def remove_data_object_tags(
    obj_id: str, keys: List[str],
    credentials: Tuple[str, User] = Depends(DatabaseWrapper.verify_key)
) -> DataObject:
    try:
        return proxies.dor.remove_tags(obj_id, authority=credentials[1].keystore, keys=keys)
    except Exception as e:
        _handle_error(e)


# --- Job Endpoints ---

@app.get("/gateway/v1/proc")
async def list_available_processors(
    _: Tuple[str, User] = Depends(DatabaseWrapper.verify_key)
) -> Dict[str, GitProcessorPointer]:
    try:
        procs: List[Processor] = proxies.rti.get_all_procs()
        return {proc.id: proc.gpp for proc in procs if proc.gpp is not None}
    except Exception as e:
        _handle_error(e)


@app.post("/gateway/v1/proc/{proc_id}")
async def submit_job(
    proc_id: str, request: SubmitJobRequest,
    credentials: Tuple[str, User] = Depends(DatabaseWrapper.verify_key)
) -> Job:
    try:
        keystore = credentials[1].keystore

        # build task input
        job_input = []
        for entry in request.task_input:
            if entry.get('type') == 'value':
                job_input.append(Task.InputValue(
                    name=entry['name'], type='value', value=entry['value']
                ))
            else:
                job_input.append(Task.InputReference(
                    name=entry['name'], type='reference',
                    obj_id=entry['obj_id'], user_signature=None, c_hash=None
                ))

        # build task output
        budget = request.budget or {}
        job_output = [
            Task.Output(
                name=entry['name'],
                owner_iid=keystore.identity.id,
                restricted_access=request.output_access_restricted,
                content_encrypted=request.output_content_encrypted,
                target_node_iid=proxies.default_target_node_iid
            ) for entry in request.task_output
        ]

        task = Task(
            proc_id=proc_id,
            user_iid=keystore.identity.id,
            input=job_input,
            output=job_output,
            budget=ResourceDescriptor(
                vcpus=budget.get('vcpus', 1),
                memory=budget.get('memory', 2048)
            ),
            namespace=None,
            name=request.name,
            description=request.description
        )

        jobs = proxies.rti.submit(tasks=[task], with_authorisation_by=keystore)
        return jobs[0]

    except Exception as e:
        _handle_error(e)


@app.get("/gateway/v1/job")
async def list_jobs(
    credentials: Tuple[str, User] = Depends(DatabaseWrapper.verify_key)
) -> List[Job]:
    try:
        return proxies.rti.get_jobs_by_user(authority=credentials[1].keystore)
    except Exception as e:
        _handle_error(e)


@app.get("/gateway/v1/job/{job_id}")
async def get_job_status(
    job_id: str, credentials: Tuple[str, User] = Depends(DatabaseWrapper.verify_key)
) -> JobStatus:
    try:
        return proxies.rti.get_job_status(job_id, with_authorisation_by=credentials[1].keystore)
    except Exception as e:
        _handle_error(e)


@app.delete("/gateway/v1/job/{job_id}")
async def cancel_job(
    job_id: str, credentials: Tuple[str, User] = Depends(DatabaseWrapper.verify_key)
) -> JobStatus:
    try:
        return proxies.rti.cancel_job(job_id, with_authorisation_by=credentials[1].keystore)
    except Exception as e:
        _handle_error(e)
