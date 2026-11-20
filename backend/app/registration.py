import asyncio
import json
import re
from dataclasses import dataclass
from unicodedata import normalize

import httpx
from geonamescache import GeonamesCache
from tldextract import TLDExtract

from watchtower.transport import verified_tls_context
from watchtower.urls import resolve_public


IANA_RDAP = "https://data.iana.org/rdap/dns.json"
RDAP_TYPE = "application/rdap+json"
MAX_RESPONSE_BYTES = 512 * 1024
LOOKUP_TIMEOUT = 5
LOOKUP_ERRORS = (
    OSError, httpx.HTTPError, httpx.InvalidURL, ValueError, KeyError, AttributeError, TypeError, RecursionError
)
DNS_NAME = re.compile(r"[a-z0-9-]{1,63}(?:\.[a-z0-9-]{1,63})+")
PRIVACY_PROXY = re.compile(
    r"\b(?:domains by proxy|registration private|withheld for privacy|contact privacy|"
    r"domain privacy|whois\s*privacy|whoisguard|privacyguardian|perfect privacy|"
    r"privacy\s*protect\w*|privacy service|identity protection|gdpr masked)\b",
    re.I,
)
extract_domain = TLDExtract(cache_dir=None, suffix_list_urls=())
geonames = GeonamesCache(min_city_population=5000)


@dataclass(frozen=True)
class RegistrationLocation:
    city: str | None
    country: str | None
    country_code: str | None
    latitude: float | None = None
    longitude: float | None = None


def address_field(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split())
    if not value or len(value) > 120 or not any(char.isalpha() for char in value):
        return ""
    if re.search(r"redact|withheld|not disclosed|not available|data protected|^n/?a$|^none$|^null$", value, re.I):
        return ""
    return value


def rdap_address(record: dict) -> tuple[str, str]:
    for entity in record.get("entities", []):
        roles = entity.get("roles", [])
        if "registrant" not in roles or "proxy" in roles:
            continue
        card = entity.get("vcardArray")
        if not isinstance(card, list) or len(card) != 2 or card[0] != "vcard":
            continue
        if any(name in ("fn", "org") and PRIVACY_PROXY.search(str(value)) for name, _, _, value in card[1]):
            continue
        for name, params, _, value in card[1]:
            if name != "adr" or not isinstance(value, list) or len(value) < 7:
                continue
            city = address_field(value[3])
            country = address_field(params.get("cc")) or address_field(value[6])
            if city or country:
                return city, country
    return "", ""


def whois_field(record: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:[ \t]*([^\r\n]*)", record, re.I | re.M)
    return match[1].strip() if match else ""


async def whois_query(server: str, query: str) -> str:
    address = await resolve_public(server, 43)
    reader, writer = await asyncio.open_connection(address, 43)
    try:
        writer.write((query + "\r\n").encode("ascii"))
        await writer.drain()
        body = bytearray()
        while chunk := await reader.read(8192):
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("WHOIS response exceeds the size limit")
        return body.decode("utf-8", errors="replace")
    finally:
        writer.close()


async def fetch_json(client: httpx.AsyncClient, url: httpx.URL) -> dict:
    if url.scheme != "https" or not url.host or url.userinfo or url.port not in (None, 443):
        raise ValueError("Registration lookups require a public HTTPS endpoint")
    # Pin the resolved address and preserve TLS verification for the original hostname.
    address = await resolve_public(url.host, 443)
    request = httpx.Request(
        "GET",
        url.copy_with(host=address),
        headers={
            "Host": url.netloc.decode("ascii"),
            "Accept": f"{RDAP_TYPE}, application/json",
            "Accept-Encoding": "identity",
        },
        extensions={"sni_hostname": url.raw_host.decode("ascii")},
    )
    response = await client.send(request, stream=True)
    try:
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=8192):
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("RDAP response exceeds the size limit")
        record = json.loads(body)
        if not isinstance(record, dict):
            raise ValueError("Expected a registration object")
        return record
    finally:
        await response.aclose()


async def registration_address(domain: str) -> tuple[str, str]:
    tls = verified_tls_context()
    tls.load_default_certs()
    async with httpx.AsyncClient(
        verify=tls, trust_env=False, limits=httpx.Limits(max_keepalive_connections=0)
    ) as client:
        bootstrap = await fetch_json(client, httpx.URL(IANA_RDAP))
        matches = (
            (len(suffix), endpoint)
            for suffixes, endpoints in bootstrap["services"]
            for suffix in suffixes
            if domain == suffix or domain.endswith("." + suffix)
            for endpoint in endpoints
            if endpoint.startswith("https://")
        )
        _, endpoint = max(matches, key=lambda match: match[0], default=(0, ""))
        if not endpoint:
            tld = domain.rsplit(".", 1)[-1]
            delegation = await whois_query("whois.iana.org", tld)
            server = whois_field(delegation, "whois").lower().rstrip(".")
            if not DNS_NAME.fullmatch(server):
                return "", ""
            record = await whois_query(server, domain)
            if any(
                PRIVACY_PROXY.search(whois_field(record, field))
                for field in ("Registrant Name", "Registrant Organization")
            ):
                return "", ""
            return (
                address_field(whois_field(record, "Registrant City")),
                address_field(whois_field(record, "Registrant Country")),
            )

        query = httpx.URL(endpoint.rstrip("/") + "/domain/" + domain)
        record = await fetch_json(client, query)
        city, country = rdap_address(record)
        if city and country:
            return city, country

        # Thin registries publish a related link to the registrar's record for this domain.
        for link in record.get("links", []):
            if link.get("rel") != "related" or link.get("type") != RDAP_TYPE:
                continue
            try:
                referral = httpx.URL(link["href"])
                if referral != query and referral.path.lower().rstrip("/").endswith("/domain/" + domain):
                    async with asyncio.timeout(1.5):
                        registrar_city, registrar_country = rdap_address(await fetch_json(client, referral))
                    if registrar_country or (registrar_city and not country):
                        return registrar_city, registrar_country
                    break
            except LOOKUP_ERRORS:
                break
        return city, country


def name_key(value: str) -> str:
    return normalize("NFC", value).casefold()


def geocode(city: str, country: str) -> RegistrationLocation | None:
    countries = geonames.get_countries()
    code = "GB" if country.upper() == "UK" else country.upper()
    if country and code not in countries:
        code = next((key for key, data in countries.items() if name_key(data["name"]) == name_key(country)), "")
    country_name = countries[code]["name"] if code else country or None
    if not city and not country_name:
        return None
    if city and code:
        name = name_key(city)
        places = [place for place in geonames.get_cities().values() if place["countrycode"] == code]
        matches = [place for place in places if name_key(place["name"]) == name]
        if not matches:
            matches = [place for place in places if any(name_key(alias) == name for alias in place["alternatenames"])]
        if len(matches) == 1:
            place = matches[0]
            return RegistrationLocation(city, country_name, code, place["latitude"], place["longitude"])
    return RegistrationLocation(city or None, country_name, code or None)


async def lookup_registration(url: str) -> RegistrationLocation | None:
    try:
        parsed = httpx.URL(url)
        host = parsed.raw_host.decode("ascii").lower().rstrip(".")
        if parsed.scheme not in ("http", "https") or parsed.userinfo or not DNS_NAME.fullmatch(host):
            return None
        domain = extract_domain(host).top_domain_under_public_suffix
        if not domain:
            return None
        async with asyncio.timeout(LOOKUP_TIMEOUT):
            city, country = await registration_address(domain)
    except LOOKUP_ERRORS:
        return None
    if city or country:
        return await asyncio.to_thread(geocode, city, country)
    return None
