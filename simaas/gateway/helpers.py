import json
from typing import Dict, List, Optional

import requests

from simaas.dor.schemas import GitProcessorPointer, DataObject
from simaas.rti.schemas import JobStatus, Job

GATEWAY_PREFIX = "/gateway/v1"


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def _url(address: str, path: str) -> str:
    return f"{address}{GATEWAY_PREFIX}{path}"


def upload(address: str, api_key: str, content_path: str, data_type: str, data_format: str,
           access_restricted: bool = True, content_encrypted: bool = False,
           tags: Dict[str, str] = None) -> DataObject:
    body = {
        'data_type': data_type,
        'data_format': data_format,
        'access_restricted': access_restricted,
        'content_encrypted': content_encrypted,
        'tags': tags if tags else None
    }

    with open(content_path, 'rb') as f:
        response = requests.post(
            url=_url(address, "/data"),
            headers=_headers(api_key),
            data={'body': json.dumps(body)},
            files={'attachment': f}
        )
        response.raise_for_status()
        return DataObject.model_validate(response.json())


def download(address: str, api_key: str, obj_id: str, content_path: str) -> None:
    with requests.get(
        url=_url(address, f"/data/{obj_id}/content"),
        headers=_headers(api_key),
        stream=True
    ) as response:
        response.raise_for_status()
        with open(content_path, "wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                file.write(chunk)


def search(address: str, api_key: str, patterns: List[str] = None, data_type: str = None,
           data_format: str = None, c_hashes: List[str] = None) -> List[DataObject]:
    response = requests.get(
        url=_url(address, "/data"),
        headers=_headers(api_key),
        params={
            "patterns": ','.join(patterns) if patterns else None,
            "data_type": data_type,
            "data_format": data_format,
            "c_hashes": ','.join(c_hashes) if c_hashes else None
        }
    )
    response.raise_for_status()
    return [DataObject.model_validate(obj) for obj in response.json()]


def delete(address: str, api_key: str, obj_id: str) -> DataObject:
    response = requests.delete(
        url=_url(address, f"/data/{obj_id}"),
        headers=_headers(api_key)
    )
    response.raise_for_status()
    return DataObject.model_validate(response.json())


def meta(address: str, api_key: str, obj_id: str) -> DataObject:
    response = requests.get(
        url=_url(address, f"/data/{obj_id}/meta"),
        headers=_headers(api_key)
    )
    response.raise_for_status()
    return DataObject.model_validate(response.json())


def tag(address: str, api_key: str, obj_id: str, tags: Dict[str, str]) -> DataObject:
    tag_list: List[DataObject.Tag] = [DataObject.Tag(key=k, value=v) for k, v in tags.items()]
    response = requests.put(
        url=_url(address, f"/data/{obj_id}/tags"),
        headers=_headers(api_key),
        json=[t.model_dump() for t in tag_list]
    )
    response.raise_for_status()
    return DataObject.model_validate(response.json())


def untag(address: str, api_key: str, obj_id: str, keys: List[str]) -> DataObject:
    response = requests.delete(
        url=_url(address, f"/data/{obj_id}/tags"),
        headers=_headers(api_key),
        json=keys
    )
    response.raise_for_status()
    return DataObject.model_validate(response.json())


def available_procs(address: str, api_key: str) -> Dict[str, GitProcessorPointer]:
    response = requests.get(
        url=_url(address, "/proc"),
        headers=_headers(api_key)
    )
    response.raise_for_status()
    return {proc_id: GitProcessorPointer.model_validate(gpp) for proc_id, gpp in response.json().items()}


def submit(address: str, api_key: str, proc_id: str, task_input: List[dict], task_output: List[dict],
           name: str = None, description: str = None,
           budget: Optional[Dict[str, int]] = None,
           output_access_restricted: bool = True,
           output_content_encrypted: bool = False) -> Job:
    body = {
        'task_input': task_input,
        'task_output': task_output,
        'name': name,
        'description': description,
        'budget': budget,
        'output_access_restricted': output_access_restricted,
        'output_content_encrypted': output_content_encrypted,
    }

    response = requests.post(
        url=_url(address, f"/proc/{proc_id}"),
        headers=_headers(api_key),
        json=body
    )
    response.raise_for_status()
    return Job.model_validate(response.json())


def jobs(address: str, api_key: str) -> List[Job]:
    response = requests.get(
        url=_url(address, "/job"),
        headers=_headers(api_key)
    )
    response.raise_for_status()
    return [Job.model_validate(job) for job in response.json()]


def job_status(address: str, api_key: str, job_id: str) -> JobStatus:
    response = requests.get(
        url=_url(address, f"/job/{job_id}"),
        headers=_headers(api_key)
    )
    response.raise_for_status()
    return JobStatus.model_validate(response.json())


def cancel(address: str, api_key: str, job_id: str) -> JobStatus:
    response = requests.delete(
        url=_url(address, f"/job/{job_id}"),
        headers=_headers(api_key)
    )
    response.raise_for_status()
    return JobStatus.model_validate(response.json())
