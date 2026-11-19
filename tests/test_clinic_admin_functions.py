"""SECURITY DEFINER functions that create and reset a clinic's CLINIC_ADMIN."""

from uuid import UUID

import psycopg
import pytest
from psycopg import errors

from tests.helpers import (
    Tenant,
    act_as,
    act_as_superuser,
    add_membership,
    digest,
    make_clinic,
    make_tenant,
    make_user,
    scalar,
)

CREATE = (
    "SELECT user_id, invitation_id FROM iam.create_first_clinic_admin("
    "%s, %s, %s, %s, %s, now() + interval '7 days')"
)
RESET = (
    "SELECT iam.reset_clinic_admin_access(%s, %s, %s, %s, now() + interval '7 days')"
)
FUNCTIONS = (
    "iam.create_first_clinic_admin(uuid, varchar, varchar, char, char, timestamptz)",
    "iam.reset_clinic_admin_access(uuid, uuid, char, char, timestamptz)",
)


@pytest.fixture
def platform_admin(db: psycopg.Connection) -> UUID:
    user_id = make_user(db, "platform-admin@example.test")
    add_membership(db, user_id, None, "PLATFORM_ADMIN")
    return user_id


@pytest.fixture
def clinic_id(db: psycopg.Connection) -> UUID:
    return make_clinic(db, "Clinica sin administrador")


@pytest.fixture
def tenant(db: psycopg.Connection) -> Tenant:
    return make_tenant(db, "intruso")


def _create(
    db: psycopg.Connection,
    platform_admin: UUID,
    clinic_id: UUID,
    email: str,
    key: str = "new",
) -> tuple[UUID, UUID]:
    """Create a CLINIC_ADMIN as PLATFORM_ADMIN and return (user_id, invitation_id)."""
    act_as(db, role="PLATFORM_ADMIN", user_id=platform_admin)
    row = db.execute(
        CREATE,
        (clinic_id, email, "Nueva Admin", digest(f"code-{key}"), "0" * 16),
    ).fetchone()
    assert row is not None
    user_id, invitation_id = row
    assert isinstance(user_id, UUID)
    assert isinstance(invitation_id, UUID)
    return user_id, invitation_id


def test_platform_admin_creates_the_first_clinic_admin(
    db: psycopg.Connection, platform_admin: UUID, clinic_id: UUID
) -> None:
    user_id, invitation_id = _create(
        db, platform_admin, clinic_id, "  Nueva.Admin@Example.test "
    )
    act_as_superuser(db)

    user = db.execute(
        "SELECT email, email_normalized, status, token_version, password_hash"
        "  FROM iam.users WHERE id = %s",
        (user_id,),
    ).fetchone()
    assert user == (
        "Nueva.Admin@Example.test",
        "nueva.admin@example.test",
        "INVITED",
        0,
        None,
    )
    membership = db.execute(
        "SELECT m.clinic_id, r.code, m.is_active"
        "  FROM iam.memberships AS m JOIN iam.roles AS r ON r.id = m.role_id"
        " WHERE m.user_id = %s",
        (user_id,),
    ).fetchone()
    assert membership == (clinic_id, "CLINIC_ADMIN", True)
    profile = db.execute(
        "SELECT clinic_id, full_name FROM clinic.staff_profiles WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    assert profile == (clinic_id, "Nueva Admin")
    invitation = db.execute(
        "SELECT clinic_id, user_id, purpose, channel, status, delivery_status,"
        "       issued_by, code_hash"
        "  FROM iam.credential_invitations WHERE id = %s",
        (invitation_id,),
    ).fetchone()
    assert invitation == (
        clinic_id,
        user_id,
        "STAFF_ACTIVATION",
        "EMAIL",
        "PENDING",
        "PENDING",
        platform_admin,
        digest("code-new"),
    )
    # The API writes the command audit; the function itself writes nothing.
    assert (
        scalar(
            db,
            "SELECT count(*) FROM audit.audit_log WHERE clinic_id = %s",
            (clinic_id,),
        )
        == 0
    )


@pytest.mark.parametrize("caller", ["clinic_admin", "no_role"])
def test_only_platform_admin_creates_the_first_admin(
    db: psycopg.Connection, tenant: Tenant, clinic_id: UUID, caller: str
) -> None:
    if caller == "clinic_admin":
        act_as(
            db,
            clinic_id=tenant.clinic_id,
            user_id=tenant.admin_id,
            role="CLINIC_ADMIN",
        )
    else:
        act_as(db)
    with (
        pytest.raises(errors.RaiseException, match="only PLATFORM_ADMIN"),
        db.transaction(),
    ):
        db.execute(
            CREATE,
            (clinic_id, "intruso@example.test", "Intruso", digest("x"), "0" * 16),
        )
    act_as_superuser(db)
    assert (
        scalar(
            db, "SELECT count(*) FROM iam.users WHERE email = 'intruso@example.test'"
        )
        == 0
    )


@pytest.mark.parametrize("clinic_state", ["missing", "suspended"])
def test_clinic_must_exist_and_be_active(
    db: psycopg.Connection, platform_admin: UUID, clinic_state: str
) -> None:
    if clinic_state == "missing":
        clinic_id = scalar(db, "SELECT uuidv7()")
        message = "clinic not found"
    else:
        clinic_id = make_clinic(db, "Clinica suspendida")
        db.execute(
            "UPDATE clinic.clinics SET status = 'SUSPENDED', suspended_at = now(),"
            " suspended_reason = 'Contrato vencido en pruebas' WHERE id = %s",
            (clinic_id,),
        )
        message = "clinic is not ACTIVE"
    assert isinstance(clinic_id, UUID)
    act_as(db, role="PLATFORM_ADMIN", user_id=platform_admin)
    with pytest.raises(errors.RaiseException, match=message), db.transaction():
        db.execute(
            CREATE,
            (clinic_id, "admin@example.test", "Admin", digest("x"), "0" * 16),
        )
    act_as_superuser(db)


def test_duplicate_email_is_a_unique_violation(
    db: psycopg.Connection, platform_admin: UUID, clinic_id: UUID
) -> None:
    _create(db, platform_admin, clinic_id, "repetido@example.test", key="first")
    with pytest.raises(errors.UniqueViolation), db.transaction():
        _create(db, platform_admin, clinic_id, "repetido@example.test", key="second")
    act_as_superuser(db)


def test_reset_revokes_access_and_issues_a_new_invitation(
    db: psycopg.Connection, platform_admin: UUID, clinic_id: UUID
) -> None:
    user_id, old_invitation = _create(
        db, platform_admin, clinic_id, "reinicio@example.test"
    )
    act_as_superuser(db)
    db.execute("UPDATE iam.users SET status = 'ACTIVE' WHERE id = %s", (user_id,))
    token_id = scalar(
        db,
        "INSERT INTO iam.refresh_tokens (user_id, clinic_id, family_id, token_hash,"
        " expires_at, family_expires_at)"
        " VALUES (%s, %s, uuidv7(), %s, now() + interval '12 hours',"
        " now() + interval '12 hours') RETURNING id",
        (user_id, clinic_id, digest("refresh-reinicio")),
    )
    factor_id = scalar(
        db,
        "INSERT INTO iam.mfa_factors (user_id, factor_type, secret_ciphertext,"
        " key_id, confirmed_at) VALUES (%s, 'TOTP', 'secret', %s, now())"
        " RETURNING id",
        (user_id, "0" * 16),
    )

    act_as(db, role="PLATFORM_ADMIN", user_id=platform_admin)
    new_invitation = scalar(
        db,
        RESET,
        (clinic_id, user_id, digest("code-reset"), "0" * 16),
    )
    act_as_superuser(db)

    assert isinstance(new_invitation, UUID)
    assert db.execute(
        "SELECT status, revoked_by FROM iam.credential_invitations WHERE id = %s",
        (old_invitation,),
    ).fetchone() == ("REVOKED", platform_admin)
    assert db.execute(
        "SELECT purpose, channel, status, clinic_id, user_id"
        "  FROM iam.credential_invitations WHERE id = %s",
        (new_invitation,),
    ).fetchone() == ("STAFF_ACTIVATION", "EMAIL", "PENDING", clinic_id, user_id)
    assert (
        scalar(
            db,
            "SELECT count(*) FROM iam.credential_invitations"
            " WHERE user_id = %s AND status IN ('PENDING', 'VERIFIED')",
            (user_id,),
        )
        == 1
    )
    assert db.execute(
        "SELECT revoked_at IS NOT NULL, revoked_reason"
        "  FROM iam.refresh_tokens WHERE id = %s",
        (token_id,),
    ).fetchone() == (True, "MFA_RESET")
    assert (
        scalar(
            db,
            "SELECT revoked_at IS NOT NULL FROM iam.mfa_factors WHERE id = %s",
            (factor_id,),
        )
        is True
    )
    assert db.execute(
        "SELECT status, token_version FROM iam.users WHERE id = %s", (user_id,)
    ).fetchone() == ("INVITED", 1)
    assert (
        scalar(
            db,
            "SELECT count(*) FROM audit.audit_log WHERE clinic_id = %s",
            (clinic_id,),
        )
        == 0
    )


@pytest.mark.parametrize("target", ["doctor", "admin_of_other_clinic"])
def test_reset_refuses_users_that_are_not_admins_of_the_clinic(
    db: psycopg.Connection, platform_admin: UUID, target: str
) -> None:
    tenant = make_tenant(db, "reinicio")
    other = make_tenant(db, "otra")
    target_id = tenant.doctor_id if target == "doctor" else other.admin_id
    act_as(db, role="PLATFORM_ADMIN", user_id=platform_admin)
    with pytest.raises(errors.RaiseException, match="not found"), db.transaction():
        db.execute(
            RESET,
            (tenant.clinic_id, target_id, digest("code-x"), "0" * 16),
        )
    act_as_superuser(db)


def test_reset_never_reactivates_a_deactivated_admin(
    db: psycopg.Connection, platform_admin: UUID, clinic_id: UUID
) -> None:
    user_id, _ = _create(db, platform_admin, clinic_id, "baja@example.test")
    act_as_superuser(db)
    db.execute(
        "UPDATE iam.users SET status = 'DEACTIVATED',"
        " status_reason = 'Baja por fin de contrato' WHERE id = %s",
        (user_id,),
    )
    act_as(db, role="PLATFORM_ADMIN", user_id=platform_admin)
    with (
        pytest.raises(errors.RaiseException, match="cannot be reset"),
        db.transaction(),
    ):
        db.execute(
            RESET,
            (clinic_id, user_id, digest("code-baja"), "0" * 16),
        )
    act_as_superuser(db)


@pytest.mark.parametrize("function", FUNCTIONS)
def test_functions_belong_to_definer_and_fix_search_path(
    db: psycopg.Connection, function: str
) -> None:
    row = db.execute(
        "SELECT pg_get_userbyid(proowner), prosecdef, proconfig"
        " FROM pg_proc WHERE oid = %s::regprocedure",
        (function,),
    ).fetchone()
    assert row is not None
    owner, security_definer, config = row
    assert owner == "insumap_definer"
    assert security_definer is True
    assert "search_path=pg_catalog" in config


@pytest.mark.parametrize("function", FUNCTIONS)
def test_only_the_application_may_execute_them(
    db: psycopg.Connection, function: str
) -> None:
    assert (
        scalar(
            db,
            "SELECT has_function_privilege('insumap_app', %s::regprocedure, 'EXECUTE')",
            (function,),
        )
        is True
    )
    assert (
        scalar(
            db,
            "SELECT has_function_privilege('insumap_readonly', %s::regprocedure,"
            " 'EXECUTE')",
            (function,),
        )
        is False
    )
