"""Small HTTP-only GraphQL client shared by local verification runners."""


def graphql(client, endpoint, query, variables=None, token=None):
    headers = {"Authorization": "Bearer " + token} if token else {}
    response = client.post(endpoint, json={"query": query, "variables": variables or {}}, headers=headers)
    response.raise_for_status()
    body = response.json()
    if body.get("errors") or not isinstance(body.get("data"), dict):
        raise RuntimeError(f"GraphQL operation failed: {body}")
    return body["data"]
