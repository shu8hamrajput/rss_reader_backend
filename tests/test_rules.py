_CONDITION = {"field": "title", "op": "contains", "value": "breaking"}
_ACTION = {"type": "add_tag", "value": "important"}


def test_create_rule(client, auth_headers):
    resp = client.post(
        "/api/v1/rules",
        json={"name": "Tag breaking news", "conditions": [_CONDITION], "actions": [_ACTION]},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Tag breaking news"
    assert body["conditions"] == [_CONDITION]
    assert body["actions"] == [_ACTION]
    assert body["is_active"] is True


def test_create_rule_empty_conditions(client, auth_headers):
    resp = client.post(
        "/api/v1/rules",
        json={"name": "Empty", "conditions": [], "actions": [_ACTION]},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_create_rule_empty_actions(client, auth_headers):
    resp = client.post(
        "/api/v1/rules",
        json={"name": "Empty", "conditions": [_CONDITION], "actions": []},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_create_rule_invalid_field_op_combo(client, auth_headers):
    resp = client.post(
        "/api/v1/rules",
        json={"name": "Bad combo", "conditions": [{"field": "title", "op": "gt", "value": "x"}], "actions": [_ACTION]},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_create_rule_plan_limit(client, auth_headers):
    for i in range(3):
        resp = client.post(
            "/api/v1/rules",
            json={"name": f"Rule {i}", "conditions": [_CONDITION], "actions": [_ACTION]},
            headers=auth_headers,
        )
        assert resp.status_code == 201

    resp = client.post(
        "/api/v1/rules",
        json={"name": "One too many", "conditions": [_CONDITION], "actions": [_ACTION]},
        headers=auth_headers,
    )
    assert resp.status_code == 403


def test_list_rules_ordered(client, auth_headers):
    client.post("/api/v1/rules", json={"name": "First", "conditions": [_CONDITION], "actions": [_ACTION]}, headers=auth_headers)
    client.post("/api/v1/rules", json={"name": "Second", "conditions": [_CONDITION], "actions": [_ACTION]}, headers=auth_headers)

    resp = client.get("/api/v1/rules", headers=auth_headers)
    assert resp.status_code == 200
    names = [r["name"] for r in resp.json()]
    assert names == ["First", "Second"]


def test_update_rule(client, auth_headers):
    created = client.post(
        "/api/v1/rules",
        json={"name": "Original", "conditions": [_CONDITION], "actions": [_ACTION]},
        headers=auth_headers,
    ).json()

    new_condition = {"field": "feed_id", "op": "eq", "value": 1}
    resp = client.patch(
        f"/api/v1/rules/{created['id']}",
        json={"name": "Renamed", "is_active": False, "conditions": [new_condition]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["is_active"] is False
    assert body["conditions"] == [new_condition]
    assert body["actions"] == [_ACTION]


def test_update_rule_empty_conditions_rejected(client, auth_headers):
    created = client.post(
        "/api/v1/rules",
        json={"name": "Original", "conditions": [_CONDITION], "actions": [_ACTION]},
        headers=auth_headers,
    ).json()

    resp = client.patch(f"/api/v1/rules/{created['id']}", json={"conditions": []}, headers=auth_headers)
    assert resp.status_code == 422


def test_update_rule_not_owned_returns_404(client, auth_headers, other_auth_headers):
    created = client.post(
        "/api/v1/rules",
        json={"name": "Other's rule", "conditions": [_CONDITION], "actions": [_ACTION]},
        headers=other_auth_headers,
    ).json()

    resp = client.patch(f"/api/v1/rules/{created['id']}", json={"name": "Hijacked"}, headers=auth_headers)
    assert resp.status_code == 404


def test_delete_rule(client, auth_headers):
    created = client.post(
        "/api/v1/rules",
        json={"name": "To delete", "conditions": [_CONDITION], "actions": [_ACTION]},
        headers=auth_headers,
    ).json()

    resp = client.delete(f"/api/v1/rules/{created['id']}", headers=auth_headers)
    assert resp.status_code == 204

    resp = client.get("/api/v1/rules", headers=auth_headers)
    assert resp.json() == []


def test_delete_rule_not_owned_returns_404(client, auth_headers, other_auth_headers):
    created = client.post(
        "/api/v1/rules",
        json={"name": "Other's rule", "conditions": [_CONDITION], "actions": [_ACTION]},
        headers=other_auth_headers,
    ).json()

    resp = client.delete(f"/api/v1/rules/{created['id']}", headers=auth_headers)
    assert resp.status_code == 404
