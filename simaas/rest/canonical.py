from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


REST_AUTH_DOMAIN = b'simaas-rest-auth:v1:'


def canonical_auth_url(url: str) -> str:
    """Stable representation of ``METHOD:URL`` so signer and verifier hash the same bytes.

    Normalises method case, lowercases scheme + host, strips a trailing slash
    from the path (root excepted), and sorts query parameters alphabetically.
    """
    method, _, raw = url.partition(':')
    method = method.upper()
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path
    if len(path) > 1 and path.endswith('/'):
        path = path[:-1]
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True))) if parts.query else ''
    normalised = urlunsplit((scheme, netloc, path, query, ''))
    return f"{method}:{normalised}"
