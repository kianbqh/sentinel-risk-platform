"""Validate the actual authenticated response used to seed a bounded ZAP scan."""
import json
import re


def validate_seed(response, expected_user_id):
    if not isinstance(response, list) or len(response) != 1:
        raise RuntimeError("Authenticated GraphQL seed did not return one response")
    header = response[0].get("responseHeader", "")
    if not re.match(r"^HTTP/\S+\s+2\d\d(?:\s|$)", header):
        raise RuntimeError("Authenticated GraphQL seed did not return HTTP success")
    try:
        body = json.loads(response[0]["responseBody"])
        me = body["data"]["me"]
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("Authenticated GraphQL seed returned an invalid body") from error
    if body.get("errors") or not isinstance(me, dict) or str(me.get("id")) != str(expected_user_id):
        raise RuntimeError("Authenticated GraphQL seed did not identify the scanner analyst")
    return str(me["id"])
