def requires_authentication(func):
    func._require_authentication = True
    return func


def requires_ownership(func):
    func._dor_requires_ownership = True
    return func


def requires_access(func):
    func._dor_requires_access = True
    return func


def requires_tasks_supported(func):
    func._rti_requires_tasks_supported = True
    return func


def requires_proc_deployed(func):
    func._rti_requires_proc_deployed = True
    return func


def requires_proc_not_busy(func):
    func._rti_requires_proc_not_busy = True
    return func


def requires_node_ownership_if_strict(func):
    func._rti_node_ownership_if_strict = True
    return func


def requires_job_or_node_ownership(func):
    func._rti_job_or_node_ownership = True
    return func


def requires_batch_or_node_ownership(func):
    func._rti_batch_or_node_ownership = True
    return func


