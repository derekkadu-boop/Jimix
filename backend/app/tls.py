import asyncio
import ipaddress
import logging
import socket
import ssl
from pathlib import Path

import certifi
import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID
from cryptography.x509.verification import PolicyBuilder, Store, VerificationError

log = logging.getLogger("watchtower.tls")
CONTEXT = ssl.create_default_context(cafile=certifi.where())
ROOTS = Store(x509.load_pem_x509_certificates(Path(certifi.where()).read_bytes()))
MAX_CERT_BYTES = 64 * 1024
MAX_DEPTH = 3
_locks: dict[str, asyncio.Lock] = {}
_results: dict[str, bool] = {}


def incomplete_chain(exc: Exception) -> bool:
    text = str(exc)
    return "CERTIFICATE_VERIFY_FAILED" in text and "unable to get local issuer certificate" in text


def issuer_url(cert: x509.Certificate) -> str | None:
    try:
        access = cert.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS)
    except x509.ExtensionNotFound:
        return None
    for item in access.value:
        if item.access_method == AuthorityInformationAccessOID.CA_ISSUERS:
            url = item.access_location.value
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                return url
    return None


def _load(body: bytes) -> x509.Certificate | None:
    for load in (x509.load_der_x509_certificate, x509.load_pem_x509_certificate):
        try:
            return load(body)
        except ValueError:
            continue
    return None


def verified_path(host: str, leaf: x509.Certificate, intermediates: list[x509.Certificate]) -> list:
    try:
        ipaddress.ip_address(host)
        return []
    except ValueError:
        pass
    verifier = PolicyBuilder().store(ROOTS).build_server_verifier(x509.DNSName(host))
    try:
        return verifier.verify(leaf, intermediates)
    except VerificationError:
        return []


def _leaf(host: str, port: int) -> x509.Certificate:
    probe = ssl.create_default_context()
    probe.check_hostname = False
    probe.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=10) as raw, probe.wrap_socket(raw, server_hostname=host) as tls:
        return x509.load_der_x509_certificate(tls.getpeercert(binary_form=True))


async def _download(url: str) -> x509.Certificate | None:
    from .fetch import check_public, wait_turn

    await check_public(url)
    await wait_turn(url)
    body = bytearray()
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client, client.stream("GET", url) as resp:
        if resp.status_code != 200:
            return None
        async for chunk in resp.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_CERT_BYTES:
                return None
    return _load(bytes(body))


async def _fill(host: str, port: int) -> bool:
    leaf = await asyncio.to_thread(_leaf, host, port)
    chain: list[x509.Certificate] = []
    current = leaf
    for _ in range(MAX_DEPTH):
        url = issuer_url(current)
        if not url or not (issuer := await _download(url)) or issuer.subject == issuer.issuer:
            return False
        try:
            current.verify_directly_issued_by(issuer)
        except (ValueError, TypeError, InvalidSignature):
            return False
        chain.append(issuer)
        if path := verified_path(host, leaf, chain):
            added = [cert for cert in path[1:-1] if cert in chain]
            for cert in added:
                CONTEXT.load_verify_locations(cadata=cert.public_bytes(Encoding.DER))
            log.info("added %d verified intermediate certificate(s) for %s", len(added), host)
            return bool(added)
        current = issuer
    return False


async def fill_chain(host: str, port: int) -> bool:
    async with _locks.setdefault(host, asyncio.Lock()):
        if host not in _results:
            _results[host] = await _fill(host, port)
        return _results[host]


class ChainFillingTransport(httpx.AsyncHTTPTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return await super().handle_async_request(request)
        except httpx.ConnectError as exc:
            if request.url.scheme != "https" or not incomplete_chain(exc):
                raise
            try:
                filled = await fill_chain(request.url.host, request.url.port or 443)
            except (OSError, httpx.HTTPError, ValueError):
                filled = False
            if not filled:
                raise
            return await super().handle_async_request(request)
