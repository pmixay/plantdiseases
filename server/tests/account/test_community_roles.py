"""Community roles and achievement rewards in the account service."""

from __future__ import annotations

import uuid

import pytest
from app.db.models import Account
from sqlalchemy import select

from tests.account import test_account_api
from tests.account.test_account_api import ApiHarness, _signup

# Reuse the account API harness fixture.
api = test_account_api.api


def _account(api: ApiHarness, nickname: str) -> dict:
    tokens = _signup(
        api,
        provider_token=f"mock-yandex-generated-{uuid.uuid4().hex}",
        nickname=nickname,
    )
    with api.engine.connect() as connection:
        actor_id = connection.execute(
            select(Account.community_actor_id).where(Account.nickname == nickname)
        ).scalar_one()
    return {"access_token": tokens["access_token"], "actor_id": str(actor_id)}


def _internal(api: ApiHarness) -> dict:
    return {"X-Internal-Service-Token": api.client.app.state.internal_service_token}


def _roles(api: ApiHarness, actor_id: str) -> list[dict]:
    response = api.client.post(
        "/v1/internal/community/roles/lookup",
        json={"actor_id": actor_id},
        headers=_internal(api),
    )
    assert response.status_code == 200, response.text
    return response.json()["roles"]


def test_internal_role_routes_require_the_service_secret(api: ApiHarness):
    for path, body in (
        ("/v1/internal/community/roles/lookup", {"actor_id": str(uuid.uuid4())}),
        ("/v1/internal/community/roles/list", {}),
        ("/v1/internal/community/rewards", {"actor_id": str(uuid.uuid4()), "achievement_count": 1}),
    ):
        response = api.client.post(path, json=body)
        assert response.status_code == 401, path


def test_only_the_operator_creates_admins_and_admins_grant_moderators(api: ApiHarness):
    admin = _account(api, "AdminLeaf")
    member = _account(api, "MemberLeaf")
    service = api.client.app.state.community_role_service

    grant = {
        "actor_id": member["actor_id"],
        "role": "moderator",
        "scope": "aroids",
        "by_actor_id": admin["actor_id"],
    }
    refused = api.client.post(
        "/v1/internal/community/roles/grant", json=grant, headers=_internal(api)
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "admin_required"

    service.grant_by_operator(nickname="AdminLeaf", role="admin")
    assert _roles(api, admin["actor_id"]) == [{"role": "admin", "scope": "*"}]

    granted = api.client.post(
        "/v1/internal/community/roles/grant", json=grant, headers=_internal(api)
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["created"] is True
    assert _roles(api, member["actor_id"]) == [{"role": "moderator", "scope": "aroids"}]

    # The API can never mint another admin.
    admin_grant = {**grant, "role": "admin", "scope": "*"}
    assert api.client.post(
        "/v1/internal/community/roles/grant", json=admin_grant, headers=_internal(api)
    ).status_code == 422

    holders = api.client.post(
        "/v1/internal/community/roles/list", json={}, headers=_internal(api)
    ).json()["items"]
    assert {(item["nickname"], item["role"], item["scope"]) for item in holders} == {
        ("AdminLeaf", "admin", "*"),
        ("MemberLeaf", "moderator", "aroids"),
    }

    revoked = api.client.post(
        "/v1/internal/community/roles/revoke", json=grant, headers=_internal(api)
    )
    assert revoked.json() == {"revoked": True}
    assert _roles(api, member["actor_id"]) == []


def test_operator_validation_rejects_scoped_admin(api: ApiHarness):
    from app.services.community_roles import CommunityRoleError

    _account(api, "ScopedLeaf")
    service = api.client.app.state.community_role_service
    with pytest.raises(CommunityRoleError) as error:
        service.grant_by_operator(nickname="ScopedLeaf", role="admin", scope="aroids")
    assert error.value.code == "invalid_scope"
    with pytest.raises(CommunityRoleError):
        service.grant_by_operator(nickname="Nobody", role="expert")


def test_reward_discount_is_two_percent_per_achievement_capped_at_forty(api: ApiHarness):
    member = _account(api, "HelperLeaf")
    headers = api.headers(member["access_token"])

    empty = api.client.get("/v1/me/community-reward", headers=headers)
    assert empty.status_code == 200
    assert empty.json() == {
        "achievement_count": 0,
        "discount_percent": 0,
        "discount_cap_percent": 40,
        "discount_per_achievement": 2,
    }
    for count, expected in ((13, 26), (20, 40), (31, 40), (0, 0)):
        response = api.client.post(
            "/v1/internal/community/rewards",
            json={"actor_id": member["actor_id"], "achievement_count": count},
            headers=_internal(api),
        )
        assert response.json() == {"achievement_count": count, "discount_percent": expected}
    assert api.client.get("/v1/me/community-reward", headers=headers).json()[
        "discount_percent"
    ] == 0

    unknown = api.client.post(
        "/v1/internal/community/rewards",
        json={"actor_id": str(uuid.uuid4()), "achievement_count": 3},
        headers=_internal(api),
    )
    assert unknown.status_code == 404


def test_roles_of_unknown_actor_are_empty(api: ApiHarness):
    assert _roles(api, str(uuid.uuid4())) == []
