"""Remoção de IAM: MFA virtual não atribuído, usuários, grupos e roles."""

from __future__ import annotations

from typing import Any


def _delete_iam_user_attachments(iam: Any, name: str) -> None:
    for k in iam.list_access_keys(UserName=name)["AccessKeyMetadata"]:
        iam.delete_access_key(UserName=name, AccessKeyId=k["AccessKeyId"])
    try:
        iam.delete_login_profile(UserName=name)
    except iam.exceptions.NoSuchEntityException:
        pass
    except iam.exceptions.ClientError as exc:
        # Sem iam:DeleteLoginProfile (a política inline do harness tem limite de tamanho), a AWS
        # nega até quando o perfil nem existe. As tarefas não criam perfil de login; se existir,
        # o delete_user falha com DeleteConflict e o item aparece em `failed`/`remaining`.
        if exc.response.get("Error", {}).get("Code") != "AccessDenied":
            raise
    for p in iam.list_user_policies(UserName=name)["PolicyNames"]:
        iam.delete_user_policy(UserName=name, PolicyName=p)
    for p in iam.list_attached_user_policies(UserName=name)["AttachedPolicies"]:
        iam.detach_user_policy(UserName=name, PolicyArn=p["PolicyArn"])
    for g in iam.list_groups_for_user(UserName=name)["Groups"]:
        iam.remove_user_from_group(UserName=name, GroupName=g["GroupName"])


def _delete_iam_group(iam: Any, name: str) -> None:
    for u in iam.get_group(GroupName=name)["Users"]:
        iam.remove_user_from_group(GroupName=name, UserName=u["UserName"])
    for p in iam.list_group_policies(GroupName=name)["PolicyNames"]:
        iam.delete_group_policy(GroupName=name, PolicyName=p)
    for p in iam.list_attached_group_policies(GroupName=name)["AttachedPolicies"]:
        iam.detach_group_policy(GroupName=name, PolicyArn=p["PolicyArn"])
    iam.delete_group(GroupName=name)


def _delete_iam_role(iam: Any, name: str) -> None:
    for p in iam.list_attached_role_policies(RoleName=name)["AttachedPolicies"]:
        iam.detach_role_policy(RoleName=name, PolicyArn=p["PolicyArn"])
    for p in iam.list_role_policies(RoleName=name)["PolicyNames"]:
        iam.delete_role_policy(RoleName=name, PolicyName=p)
    iam.delete_role(RoleName=name)


def deleters(session: Any) -> dict[str, Any]:
    """IAM: categoria → função que apaga um item."""
    iam = session.client("iam")

    def iam_user(name: str) -> None:
        _delete_iam_user_attachments(iam, name)
        iam.delete_user(UserName=name)

    return {
        "iam_virtual_mfa_devices": lambda s: iam.delete_virtual_mfa_device(SerialNumber=s),
        "iam_users": iam_user,
        "iam_groups": lambda n: _delete_iam_group(iam, n),
        "iam_roles": lambda n: _delete_iam_role(iam, n),
    }
