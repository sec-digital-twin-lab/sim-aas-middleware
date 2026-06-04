def public_access(func):
    """Mark an endpoint as intentionally unauthenticated.

    Routes without any auth marker (this or one of the ``requires_*`` markers)
    are rejected at node startup so missing decorators can't slip into production.
    """
    func._public_access = True
    return func


def p2p_public_access(cls):
    """Mark a P2PProtocol subclass as anonymously callable.

    Protocols without either this marker or ``@p2p_requires_authentication`` are
    rejected when ``P2PService.add`` registers them, mirroring the REST endpoint
    assertion. Identity discovery, topology lookups, and similar read-only or
    self-authenticating protocols belong here.
    """
    cls._p2p_public_access = True
    return cls


def p2p_requires_authentication(cls):
    """Mark a P2PProtocol subclass as requiring a signed request.

    The server rejects unsigned requests for these protocols and passes the
    verified sender identity into ``handle()`` via the ``identity`` keyword.
    The caller side must invoke ``perform()`` with a signing keystore.
    """
    cls._p2p_requires_authentication = True
    return cls


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


