#!/usr/bin/env python3
"""
TeamUp Gym Auto-Booker
"""

import os
import sys
import json
import logging
from datetime import datetime, timedelta
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

EMAIL            = os.getenv("TEAMUP_EMAIL", "")
PASSWORD         = os.getenv("TEAMUP_PASSWORD", "")
BUSINESS_SLUG    = os.getenv("TEAMUP_BUSINESS_SLUG", "")
MEMBERSHIP_ID    = os.getenv("TEAMUP_MEMBERSHIP_ID", "")
TARGET_HOUR      = 6
BOOK_AHEAD_DAYS  = 14
BASE_URL         = "https://api.goteamup.com/v1"

def session_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }

def raise_for(resp, context):
    if not resp.ok:
        log.error("%s failed: %s %s", context, resp.status_code, resp.text[:400])
        resp.raise_for_status()

def login(email, password, business_slug):
    log.info("Logging in as %s ...", email)
    resp = requests.post(
        f"{BASE_URL}/auth/password-login",
        json={"email": email, "password": password},
    )
    raise_for(resp, "Login")
    data = resp.json()
    token = data["access_token"]

    profiles_resp = requests.get(
        f"{BASE_URL}/auth/profiles",
        headers=session_headers(token),
    )
    raise_for(profiles_resp, "Profiles")
    profiles = profiles_resp.json().get("results", [])

    provider_id = None
    for profile in profiles:
        if profile.get("slug") == business_slug or profile.get("id") == business_slug:
            provider_id = profile["id"]
            break

    if not provider_id:
        if len(profiles) == 1:
            provider_id = profiles[0]["id"]
        else:
            available = [f"{p.get('slug','?')} ({p.get('name','?')})" for p in profiles]
            raise ValueError(f"Cannot find business '{business_slug}'. Available: {available}")

    log.info("Authenticated. Provider ID: %s", provider_id)
    return token, provider_id

def find_slot(token, provider_id, target_date):
    date_str = target_date.strftime("%Y-%m-%d")
    log.info("Searching for 6am slot on %s ...", date_str)

    params = {
        "provider":  provider_id,
        "date_from": f"{date_str}T00:00:00",
        "date_to":   f"{date_str}T23:59:59",
        "page_size": 50,
    }
    resp = requests.get(
        f"{BASE_URL}/slots",
        headers=session_headers(token),
        params=params,
    )
    raise_for(resp, "List slots")
    slots = resp.json().get("results", [])
    log.info("Found %d slot(s) on %s.", len(slots), date_str)

    for slot in slots:
        start_raw = slot.get("start_time") or slot.get("start_at") or ""
        if not start_raw:
            continue
        try:
            dt = datetime.fromisoformat(start_raw)
            if dt.hour == TARGET_HOUR:
                log.info("Matched slot: id=%s start=%s", slot.get("id"), start_raw)
                return slot
        except ValueError:
            continue

    raise LookupError(f"No 6am slot found on {date_str}.")

def get_membership_id(token, provider_id):
    if MEMBERSHIP_ID:
        return MEMBERSHIP_ID
    log.info("Auto-detecting membership ...")
    resp = requests.get(
        f"{BASE_URL}/customer-memberships",
        headers=session_headers(token),
        params={"provider": provider_id, "status": "active", "page_size": 10},
    )
    raise_for(resp, "List memberships")
    memberships = resp.json().get("results", [])
    if not memberships:
        raise LookupError("No active memberships found.")
    mid = memberships[0]["id"]
    log.info("Using membership ID: %s", mid)
    return mid

def book_slot(token, slot, membership_id):
    slot_id = slot["id"]
    log.info("Booking slot %s ...", slot_id)
    resp = requests.post(
        f"{BASE_URL}/slots/{slot_id}/register",
        headers=session_headers(token),
        json={"customer_membership_id": membership_id},
    )
    if resp.status_code == 409:
        log.warning("Already booked into slot %s.", slot_id)
        return {"status": "already_booked"}
    raise_for(resp, "Book slot")
    result = resp.json()
    log.info("Booked! Registration ID: %s", result.get("id", "?"))
    return result

def main():
    missing = [k for k, v in {
        "TEAMUP_EMAIL": EMAIL,
        "TEAMUP_PASSWORD": PASSWORD,
        "TEAMUP_BUSINESS_SLUG": BUSINESS_SLUG,
    }.items() if not v]
    if missing:
        log.error("Missing env vars: %s", ", ".join(missing))
        sys.exit(1)

    today = datetime.now()
    target_date = today + timedelta(days=BOOK_AHEAD_DAYS)

    if target_date.weekday() >= 5:
        log.info("Target date %s is a weekend — nothing to book.", target_date.strftime("%Y-%m-%d"))
        return

    log.info("Booking for %s (%s)", target_date.strftime("%Y-%m-%d"), target_date.strftime("%A"))

    token, provider_id = login(EMAIL, PASSWORD, BUSINESS_SLUG)
    slot               = find_slot(token, provider_id, target_date)
    membership_id      = get_membership_id(token, provider_id)
    result             = book_slot(token, slot, membership_id)
    log.info("Done: %s", json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
