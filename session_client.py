import re
import time
import requests
from urllib.parse import urlparse

import config

_last_session = None


def create_session():
    global _last_session

    session = requests.Session()

    session.headers.update({
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    })

    _last_session = session

    return session


def get_last_session():
    return _last_session


def _inject_jwt_from_header(session, session_url, header_value):
    if not header_value:
        return False

    m = re.search(r"\bjwt=([^;\s]+)", header_value)
    if not m:
        return False

    jwt_value = m.group(1)
    host = urlparse(session_url).hostname
    try:
        if host:
            session.cookies.set("jwt", jwt_value, domain=host, path="/namesearch-server")
        else:
            session.cookies.set("jwt", jwt_value)
        return True
    except Exception:
        return False


def initialize_session(session):
    """Establish the frontend cookies and obtain a jwt cookie from SESSION_URL.

    Returns True when jwt is present in session.cookies.
    """
    session_url = config.SESSION_URL

    # load the frontend to establish base cookies
    if config.BASE_URL:
        frontend_url = f"{config.BASE_URL}/ttNameSearch/"
        try:
            session.get(
                frontend_url,
                headers={
                    "User-Agent": config.USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                verify=config.VERIFY_SSL,
                timeout=25,
            )
        except requests.RequestException:
            pass

    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    if config.BASE_URL:
        headers["Referer"] = f"{config.BASE_URL}/ttNameSearch/"

    # try GET then POST, with a few retries
    for _ in range(3):
        try:
            resp = session.get(session_url, headers=headers, verify=config.VERIFY_SSL, timeout=25)

            sc = resp.headers.get("Set-Cookie")
            if _inject_jwt_from_header(session, session_url, sc):
                return True

            # try post fallback
            try:
                post_resp = session.post(session_url, headers=headers, json={}, verify=config.VERIFY_SSL, timeout=25)
                sc2 = post_resp.headers.get("Set-Cookie")
                if _inject_jwt_from_header(session, session_url, sc2):
                    return True
            except requests.RequestException:
                pass

        except requests.RequestException:
            pass

        # small backoff
        time.sleep(1)

    return False


def validate_name(session, name, headers):
    payload = {"rvr-input-lang": "en", "CompanyName": name, "id": "NSPPublicSearch"}

    return session.post(config.VALIDATE_URL, json=payload, headers=headers, verify=config.VERIFY_SSL, timeout=25)


def search_name_reservation(session, name, headers):
    # Use the public search payload expected by the server
    payload = {"rvr-input-lang": "en", "CompanyName": name, "searchName": "ns-public-search"}

    return session.post(config.TARGET_URL, json=payload, headers=headers, verify=config.VERIFY_SSL, timeout=25)
