"""Founder-only, tenant-scoped legacy backup -> current Django import.

Supports both raw SQLite databases and INPROFIC/Django JSON fixtures. The
importer never replaces the destination database: it proves ownership of one
legacy Business, remaps old primary keys to new PostgreSQL rows, and writes
inside one atomic transaction. Global subscription/entitlement state and
ephemeral notification records are intentionally excluded.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models, transaction

from core.models import AuditLog, Business
from .models import Role, RoleModulePermission, UserBusiness, UserModulePermission
from .services import seed_business_roles


MAX_SQLITE_BYTES = 200 * 1024 * 1024
MAX_JSON_BYTES = 25 * 1024 * 1024
TENANT_APPS = {"core", "accounts", "inventory", "procurement", "production", "sales", "expenses", "commerce"}
EXCLUDED_LABELS = {
    # The destination tenant's SaaS/commercial boundary remains authoritative.
    "accounts.businessmoduleaccess",
    "accounts.businessfeatureaccess",
    "accounts.subscriptionplan",
    "accounts.subscriptionplanmodule",
    "accounts.businesssubscription",
    "accounts.subscriptionservice",
    "accounts.subscriptionpayment",
    "accounts.subscriptionpaymentsettings",
    # Special identity/role handling happens separately.
    "accounts.customuser",
    "accounts.role",
    "accounts.rolemodulepermission",
    "accounts.userbusiness",
    "accounts.usermodulepermission",
    # Historical audit object IDs cannot be safely remapped generically.
    "core.auditlog",
    # Ephemeral delivery/read state is intentionally regenerated in the new deployment.
    "commerce.commercenotification",
    "commerce.commercenotificationread",
    "commerce.commercepushsubscription",
    "commerce.commercepushdelivery",
}
SINGLETON_LABELS = {
    "commerce.commercesettings",
    "commerce.commercepaymentconfiguration",
    "production.ordernumbersequence",
    "commerce.commerceordernumbersequence",
}
BUSINESS_PROFILE_FIELDS = (
    "name", "currency_symbol", "vertical", "accent_color", "background_color",
    "tagline", "restaurant_table_service",
)


class LegacyImportError(Exception):
    pass


@dataclass
class SourceModelInfo:
    model: type[models.Model]
    columns: set[str]
    source_ids: set[int]
    missing_required: list[str]
    ignored_source_columns: list[str]
    skipped_file_fields: list[str]
    validation_errors: list[str]

    @property
    def label(self):
        return self.model._meta.label_lower


class LegacySQLite:
    def __init__(self, uploaded_file):
        self.uploaded_file = uploaded_file
        self.temp_path = None
        self.connection = None

    def __enter__(self):
        if getattr(self.uploaded_file, "size", 0) > MAX_SQLITE_BYTES:
            raise LegacyImportError("SQLite backup is larger than the 200 MB Founder Console safety limit.")
        handle = tempfile.NamedTemporaryFile(prefix="inprofic-legacy-", suffix=".sqlite3", delete=False)
        self.temp_path = handle.name
        try:
            for chunk in self.uploaded_file.chunks():
                handle.write(chunk)
        finally:
            handle.close()
        try:
            with open(self.temp_path, "rb") as source:
                if source.read(16) != b"SQLite format 3\x00":
                    raise LegacyImportError("The uploaded file is not a valid SQLite 3 database.")
            uri = f"file:{Path(self.temp_path).as_posix()}?mode=ro"
            self.connection = sqlite3.connect(uri, uri=True)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA query_only=ON")
            self.connection.execute("PRAGMA trusted_schema=OFF")
            quick_check = self.connection.execute("PRAGMA quick_check").fetchone()
            if not quick_check or quick_check[0] != "ok":
                raise LegacyImportError("SQLite integrity check failed; upload a clean backup before importing.")
            return self
        except Exception:
            if self.connection is not None:
                self.connection.close()
                self.connection = None
            Path(self.temp_path).unlink(missing_ok=True)
            raise

    def __exit__(self, exc_type, exc, tb):
        if self.connection is not None:
            self.connection.close()
        if self.temp_path:
            Path(self.temp_path).unlink(missing_ok=True)

    def tables(self):
        return {
            row[0]
            for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    def columns(self, table):
        return {row[1] for row in self.connection.execute(f'PRAGMA table_info("{table}")')}

    def rows(self, table, *, where=None, params=()):
        sql = f'SELECT * FROM "{table}"'
        if where:
            sql += f" WHERE {where}"
        return [dict(row) for row in self.connection.execute(sql, params)]

    def rows_for_ids(self, table, pk_column, ids):
        if not ids:
            return []
        result = []
        ordered = list(ids)
        for start in range(0, len(ordered), 800):
            chunk = ordered[start:start + 800]
            marks = ",".join("?" for _ in chunk)
            result.extend(
                dict(row) for row in self.connection.execute(
                    f'SELECT * FROM "{table}" WHERE "{pk_column}" IN ({marks})', chunk
                )
            )
        return result

    def ids_where_in(self, table, pk_column, fk_column, ids):
        if not ids:
            return set()
        result = set()
        ordered = list(ids)
        for start in range(0, len(ordered), 800):
            chunk = ordered[start:start + 800]
            marks = ",".join("?" for _ in chunk)
            result.update(
                row[0] for row in self.connection.execute(
                    f'SELECT "{pk_column}" FROM "{table}" WHERE "{fk_column}" IN ({marks})', chunk
                )
            )
        return result


def _model_label(model):
    return model._meta.label_lower


def _field_to_business(model):
    for field in model._meta.concrete_fields:
        if field.is_relation and field.related_model is Business:
            return field
    return None


def _tenant_models():
    all_models = [
        model for model in apps.get_models()
        if model._meta.app_label in TENANT_APPS
        and _model_label(model) not in EXCLUDED_LABELS
        and model is not Business
    ]
    selected = {model for model in all_models if _field_to_business(model)}
    changed = True
    while changed:
        changed = False
        for model in all_models:
            if model in selected:
                continue
            for field in model._meta.concrete_fields:
                if field.is_relation and field.related_model in selected:
                    selected.add(model)
                    changed = True
                    break
    return selected


def _dependency_order(selected):
    dependencies = {model: set() for model in selected}
    dependents = defaultdict(set)
    for model in selected:
        for field in model._meta.concrete_fields:
            if field.is_relation and field.related_model in selected and field.related_model is not model:
                dependencies[model].add(field.related_model)
                dependents[field.related_model].add(model)
    queue = deque(sorted((m for m, deps in dependencies.items() if not deps), key=_model_label))
    ordered = []
    while queue:
        model = queue.popleft()
        ordered.append(model)
        for child in dependents[model]:
            dependencies[child].discard(model)
            if not dependencies[child]:
                queue.append(child)
    remaining = [model for model in selected if model not in ordered]
    if remaining:
        labels = ", ".join(sorted(_model_label(model) for model in remaining))
        raise LegacyImportError(f"Current schema contains an unsupported circular tenant dependency: {labels}.")
    return ordered


def _required_missing_fields(model, source_columns):
    missing = []
    for field in model._meta.concrete_fields:
        if field.primary_key or field.auto_created or getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False):
            continue
        if isinstance(field, (models.FileField, models.ImageField)):
            continue
        if field.is_relation and field.related_model is Business:
            continue
        if field.column in source_columns:
            continue
        if field.null or field.has_default():
            continue
        missing.append(field.name)
    return missing


def _build_source_infos(db: LegacySQLite, source_business_id):
    tables = db.tables()
    selected = _tenant_models()
    ordered = _dependency_order(selected)
    infos = {}
    for model in ordered:
        table = model._meta.db_table
        if table not in tables:
            infos[model] = SourceModelInfo(model, set(), set(), [], [], [], [])
            continue
        columns = db.columns(table)
        pk_column = model._meta.pk.column
        source_ids = set()
        business_field = _field_to_business(model)
        scope_error = ""
        if business_field and business_field.column in columns:
            source_ids = {
                row[0] for row in db.connection.execute(
                    f'SELECT "{pk_column}" FROM "{table}" WHERE "{business_field.column}"=?',
                    (source_business_id,),
                )
            }
        else:
            usable_parent_fields = []
            for field in model._meta.concrete_fields:
                if not field.is_relation or field.related_model not in infos or field.column not in columns:
                    continue
                usable_parent_fields.append(field)
                source_ids |= db.ids_where_in(
                    table, pk_column, field.column, infos[field.related_model].source_ids
                )
            table_has_rows = bool(db.connection.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone())
            if table_has_rows and business_field and business_field.column not in columns:
                scope_error = f"legacy table is missing tenant column {business_field.column}"
            elif table_has_rows and not business_field and not usable_parent_fields:
                scope_error = "legacy table cannot be tenant-scoped because its current parent relation columns are missing"
        current_columns = {field.column for field in model._meta.concrete_fields}
        validation_errors = [scope_error] if scope_error else []
        if source_ids:
            sample_rows = db.rows_for_ids(table, pk_column, source_ids)
            for row in sample_rows:
                for field in model._meta.concrete_fields:
                    if (
                        field.primary_key or field.auto_created or getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False)
                        or field.is_relation or isinstance(field, (models.FileField, models.ImageField))
                        or field.column not in columns
                    ):
                        continue
                    try:
                        field.clean(_parse_scalar(field, row.get(field.column)), None)
                    except Exception as exc:
                        validation_errors.append(
                            f"row {row.get(pk_column)} · {field.name}: {exc}"
                        )
                        if len(validation_errors) >= 8:
                            break
                if len(validation_errors) >= 8:
                    break
        infos[model] = SourceModelInfo(
            model=model,
            columns=columns,
            source_ids=source_ids,
            missing_required=_required_missing_fields(model, columns),
            ignored_source_columns=sorted(columns - current_columns),
            skipped_file_fields=sorted(
                field.name for field in model._meta.concrete_fields
                if isinstance(field, (models.FileField, models.ImageField)) and field.column in columns
            ),
            validation_errors=validation_errors,
        )
    return ordered, infos


def _source_businesses(db):
    table = Business._meta.db_table
    if table not in db.tables():
        raise LegacyImportError(f"Legacy database has no {table} table; it is not an INPROFIC tenant database.")
    columns = db.columns(table)
    wanted = [name for name in ("id", "name", "slug", "vertical") if name in columns]
    if "id" not in wanted:
        raise LegacyImportError("Legacy business table has no primary-key column.")
    return [dict(row) for row in db.connection.execute(
        f'SELECT {", ".join(chr(34)+c+chr(34) for c in wanted)} FROM "{table}" ORDER BY "id"'
    )]


def _target_conflicts(target_business, ordered):
    conflicts = []
    for model in ordered:
        business_field = _field_to_business(model)
        if not business_field or _model_label(model) in SINGLETON_LABELS:
            continue
        manager = getattr(model, "raw_objects", model._base_manager)
        count = manager.filter(**{business_field.name: target_business}).count()
        if count:
            conflicts.append({"model": model._meta.verbose_name_plural.title(), "label": _model_label(model), "count": count})
    return conflicts


def _global_unique_conflicts(db, ordered, infos):
    """Find scalar unique-value collisions against the live destination DB.

    Tenant foreign keys/one-to-one fields are remapped during import and are
    therefore intentionally excluded. The dry run reports only counts/field
    names so it never echoes API keys, references, UUIDs, or other potentially
    sensitive legacy values back into the Founder Console.
    """
    conflicts = []
    for model in ordered:
        info = infos[model]
        if not info.source_ids:
            continue
        unique_fields = [
            field for field in model._meta.concrete_fields
            if field.unique
            and not field.primary_key
            and not field.is_relation
            and field.column in info.columns
            and not isinstance(field, (models.FileField, models.ImageField))
        ]
        if not unique_fields:
            continue
        rows = _source_table_rows(db, model, info)
        manager = getattr(model, "raw_objects", model._base_manager)
        for field in unique_fields:
            values = []
            seen = set()
            invalid = 0
            for row in rows:
                raw = row.get(field.column)
                if raw in (None, ""):
                    continue
                try:
                    value = field.to_python(_parse_scalar(field, raw))
                    # JSON/dict-like values cannot be safely used as members of
                    # an __in lookup and are not expected to be globally unique.
                    hash(value)
                except (TypeError, ValueError, ValidationError):
                    invalid += 1
                    continue
                if value not in seen:
                    seen.add(value)
                    values.append(value)
            if invalid:
                # Normal field validation already reports the exact schema
                # problem elsewhere; do not duplicate raw values here.
                continue
            collision_count = 0
            for start in range(0, len(values), 500):
                collision_count += manager.filter(
                    **{f"{field.name}__in": values[start:start + 500]}
                ).count()
            if collision_count:
                conflicts.append({
                    "model": model._meta.verbose_name_plural.title(),
                    "label": _model_label(model),
                    "field": field.name,
                    "count": collision_count,
                })
    return conflicts


def analyze_legacy_sqlite(uploaded_file, target_business, source_business_id=None):
    with LegacySQLite(uploaded_file) as db:
        businesses = _source_businesses(db)
        selected = None
        if source_business_id is not None:
            selected = next((row for row in businesses if int(row["id"]) == int(source_business_id)), None)
            if selected is None:
                raise LegacyImportError("The selected source tenant ID does not exist in this SQLite backup.")
        elif len(businesses) == 1:
            selected = businesses[0]
            source_business_id = selected["id"]

        report = {
            "source_businesses": businesses,
            "source_business": selected,
            "source_business_id": source_business_id,
            "target_business": target_business,
            "models": [],
            "blockers": [],
            "warnings": [],
            "ready": False,
        }
        if selected is None:
            report["blockers"].append("This backup contains multiple tenants. Enter the legacy source tenant ID shown below and run Dry run again.")
            return report

        ordered, infos = _build_source_infos(db, int(source_business_id))
        for model in ordered:
            info = infos[model]
            if info.missing_required and info.source_ids:
                report["blockers"].append(
                    f"{model._meta.verbose_name_plural.title()}: legacy schema is missing required current field(s): {', '.join(info.missing_required)}."
                )
            if info.validation_errors:
                report["blockers"].append(
                    f"{model._meta.verbose_name_plural.title()}: legacy values do not satisfy the current schema: "
                    + " | ".join(info.validation_errors)
                )
            if info.skipped_file_fields and info.source_ids:
                report["warnings"].append(
                    f"{model._meta.verbose_name_plural.title()}: file/media field(s) {', '.join(info.skipped_file_fields)} will not be copied from SQLite; upload media separately if needed."
                )
            report["models"].append({
                "label": _model_label(model),
                "name": model._meta.verbose_name_plural.title(),
                "rows": len(info.source_ids),
                "table_present": bool(info.columns),
                "missing_required": info.missing_required,
                "ignored_source_columns": info.ignored_source_columns,
                "skipped_file_fields": info.skipped_file_fields,
                "validation_errors": info.validation_errors,
            })

        conflicts = _target_conflicts(target_business, ordered)
        if conflicts:
            report["blockers"].append(
                "Destination tenant already contains operational data. Import into a fresh/empty tenant to prevent duplicate or ambiguous merges."
            )
            report["target_conflicts"] = conflicts

        unique_conflicts = _global_unique_conflicts(db, ordered, infos)
        if unique_conflicts:
            report["blockers"].append(
                "The legacy tenant contains globally unique value(s) that already exist elsewhere in the live database. Resolve these collisions before importing."
            )
            report["unique_conflicts"] = unique_conflicts
        report["ready"] = not report["blockers"]
        return report


def _parse_scalar(field, value):
    if value is None:
        return None
    if isinstance(field, models.JSONField) and isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _source_table_rows(db, model, info):
    return db.rows_for_ids(model._meta.db_table, model._meta.pk.column, info.source_ids)


def _import_identity(db, source_business_id, target_business):
    User = get_user_model()
    tables = db.tables()
    user_table = User._meta.db_table
    role_table = Role._meta.db_table
    membership_table = UserBusiness._meta.db_table
    role_perm_table = RoleModulePermission._meta.db_table
    user_perm_table = UserModulePermission._meta.db_table
    if membership_table not in tables:
        return {}, {}, {}, {"users_created": 0, "users_matched": 0, "memberships": 0}

    memberships = db.rows(membership_table, where='"business_id"=?', params=(source_business_id,))
    user_ids = {row.get("user_id") for row in memberships if row.get("user_id") is not None}
    role_ids = {row.get("role_id") for row in memberships if row.get("role_id") is not None}

    source_users = {row["id"]: row for row in db.rows_for_ids(user_table, "id", user_ids)} if user_table in tables else {}
    # User identity is global, not tenant-owned. Load the small identity index
    # once so username/email matching is case-insensitive without an N+1 query.
    existing = list(User.objects.only("id", "username", "email"))
    by_username = {user.username.casefold(): user for user in existing if user.username}
    by_email = {user.email.casefold(): user for user in existing if user.email}

    user_map = {}
    new_users = []
    new_source_ids = []
    matched = 0
    for source_id, row in source_users.items():
        username = str(row.get("username") or "").strip()
        email = str(row.get("email") or "").strip()
        u_match = by_username.get(username.casefold()) if username else None
        e_match = by_email.get(email.casefold()) if email else None
        if u_match and e_match and u_match.pk != e_match.pk:
            raise LegacyImportError(f"Legacy user {username!r} conflicts with two different existing users by username/email.")
        current = u_match or e_match
        if current:
            user_map[source_id] = current.pk
            matched += 1
            continue
        if not username:
            raise LegacyImportError(f"Legacy user #{source_id} has no username and cannot be migrated safely.")
        new_users.append(User(
            username=username,
            fullname=str(row.get("fullname") or username)[:160],
            email=email,
            phone=str(row.get("phone") or "")[:30],
            password=str(row.get("password") or "!"),
            is_active=bool(row.get("is_active", 1)),
            # Never import global platform authority from a tenant backup.
            is_staff=False,
            is_superuser=False,
        ))
        new_source_ids.append(source_id)
    if new_users:
        User.objects.bulk_create(new_users, batch_size=250)
        for source_id, user in zip(new_source_ids, new_users):
            user_map[source_id] = user.pk

    target_roles = seed_business_roles(target_business)
    target_by_key = {key: role for key, role in target_roles.items()}
    source_roles = {row["id"]: row for row in db.rows_for_ids(role_table, "id", role_ids)} if role_table in tables else {}
    role_map = {}
    custom_to_create = []
    custom_source_ids = []
    existing_custom = {role.key: role for role in Role.objects.filter(business=target_business)}
    for source_id, row in source_roles.items():
        key = str(row.get("key") or "").strip()
        if key == "superuser":
            key = "business_admin"
        target_role = existing_custom.get(key) or target_by_key.get(key)
        if target_role:
            role_map[source_id] = target_role.pk
            continue
        name = str(row.get("name") or key or f"Legacy role {source_id}")[:80]
        custom_to_create.append(Role(
            business=target_business,
            key=key or f"legacy-role-{source_id}",
            name=name,
            is_system=False,
            active=bool(row.get("active", 1)),
            visible_to_admin=bool(row.get("visible_to_admin", 1)),
        ))
        custom_source_ids.append(source_id)
    if custom_to_create:
        Role.objects.bulk_create(custom_to_create, batch_size=100)
        for source_id, role in zip(custom_source_ids, custom_to_create):
            role_map[source_id] = role.pk

    if role_perm_table in tables and role_map:
        source_role_perms = []
        for chunk_start in range(0, len(role_ids), 800):
            chunk = list(role_ids)[chunk_start:chunk_start + 800]
            if not chunk:
                continue
            marks = ",".join("?" for _ in chunk)
            source_role_perms += [dict(row) for row in db.connection.execute(
                f'SELECT * FROM "{role_perm_table}" WHERE "role_id" IN ({marks})', chunk
            )]
        perm_rows = [
            RoleModulePermission(
                role_id=role_map[row["role_id"]],
                module=row.get("module"),
                can_view=bool(row.get("can_view")),
                can_edit=bool(row.get("can_edit")),
            )
            for row in source_role_perms if row.get("role_id") in role_map and row.get("module")
        ]
        if perm_rows:
            RoleModulePermission.objects.bulk_create(
                perm_rows, update_conflicts=True,
                update_fields=["can_view", "can_edit"], unique_fields=["role", "module"], batch_size=250,
            )

    membership_objs = []
    membership_source_by_user = {}
    for row in memberships:
        mapped_user = user_map.get(row.get("user_id"))
        mapped_role = role_map.get(row.get("role_id"))
        if not mapped_user or not mapped_role:
            continue
        membership_objs.append(UserBusiness(
            user_id=mapped_user, business=target_business, role_id=mapped_role, active=bool(row.get("active", 1))
        ))
        membership_source_by_user[mapped_user] = row["id"]
    if membership_objs:
        UserBusiness.objects.bulk_create(
            membership_objs, update_conflicts=True,
            update_fields=["role", "active"], unique_fields=["user", "business"], batch_size=250,
        )
    current_memberships = UserBusiness.objects.filter(
        business=target_business, user_id__in=list(membership_source_by_user)
    )
    membership_map = {
        membership_source_by_user[membership.user_id]: membership.pk
        for membership in current_memberships
        if membership.user_id in membership_source_by_user
    }

    if user_perm_table in tables and membership_map:
        source_membership_ids = list(membership_map)
        source_perms = []
        for start in range(0, len(source_membership_ids), 800):
            chunk = source_membership_ids[start:start + 800]
            marks = ",".join("?" for _ in chunk)
            source_perms += [dict(row) for row in db.connection.execute(
                f'SELECT * FROM "{user_perm_table}" WHERE "membership_id" IN ({marks})', chunk
            )]
        user_perm_objs = [
            UserModulePermission(
                membership_id=membership_map[row["membership_id"]], module=row.get("module"),
                can_view=None if row.get("can_view") is None else bool(row.get("can_view")),
                can_edit=None if row.get("can_edit") is None else bool(row.get("can_edit")),
            )
            for row in source_perms if row.get("membership_id") in membership_map and row.get("module")
        ]
        if user_perm_objs:
            UserModulePermission.objects.bulk_create(
                user_perm_objs, update_conflicts=True,
                update_fields=["can_view", "can_edit"], unique_fields=["membership", "module"], batch_size=250,
            )

    return user_map, role_map, membership_map, {
        "users_created": len(new_users), "users_matched": matched, "memberships": len(membership_map)
    }


def _copy_business_profile(db, source_business_id, target_business):
    table = Business._meta.db_table
    columns = db.columns(table)
    row = db.connection.execute(f'SELECT * FROM "{table}" WHERE "id"=?', (source_business_id,)).fetchone()
    if not row:
        return []
    source = dict(row)
    updated = []
    for field_name in BUSINESS_PROFILE_FIELDS:
        field = Business._meta.get_field(field_name)
        if field.column in columns and source.get(field.column) is not None:
            setattr(target_business, field_name, source[field.column])
            updated.append(field_name)
    if updated:
        target_business.save(update_fields=updated)
    return updated


def _transform_row(model, row, *, target_business, mappings, user_map, role_map, membership_map):
    kwargs = {}
    deferred = []
    for field in model._meta.concrete_fields:
        if field.primary_key or field.auto_created or getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False):
            continue
        if isinstance(field, (models.FileField, models.ImageField)):
            continue
        if field.is_relation:
            old_fk = row.get(field.column)
            if field.related_model is Business:
                kwargs[field.attname] = target_business.pk
            elif field.related_model is get_user_model():
                mapped = user_map.get(old_fk)
                if mapped is None and old_fk is not None and not field.null:
                    raise LegacyImportError(f"{model._meta.label}: required user reference {old_fk} cannot be mapped.")
                kwargs[field.attname] = mapped
            elif field.related_model is Role:
                mapped = role_map.get(old_fk)
                if mapped is None and old_fk is not None and not field.null:
                    raise LegacyImportError(f"{model._meta.label}: required role reference {old_fk} cannot be mapped.")
                kwargs[field.attname] = mapped
            elif field.related_model is UserBusiness:
                mapped = membership_map.get(old_fk)
                if mapped is None and old_fk is not None and not field.null:
                    raise LegacyImportError(f"{model._meta.label}: required membership reference {old_fk} cannot be mapped.")
                kwargs[field.attname] = mapped
            elif field.related_model in mappings:
                mapped = mappings[field.related_model].get(old_fk)
                if mapped is None and old_fk is not None:
                    if field.null:
                        kwargs[field.attname] = None
                        deferred.append((field.name, field.related_model, old_fk))
                    else:
                        raise LegacyImportError(
                            f"{model._meta.label}: required {field.name} reference {old_fk} cannot be mapped."
                        )
                else:
                    kwargs[field.attname] = mapped
            elif old_fk is None or field.null:
                kwargs[field.attname] = None
            else:
                raise LegacyImportError(
                    f"{model._meta.label}: unsupported required global relation {field.name}."
                )
            continue
        if field.column in row:
            kwargs[field.name] = _parse_scalar(field, row.get(field.column))
    return kwargs, deferred


def import_legacy_sqlite(uploaded_file, target_business, source_business_id, *, actor=None):
    # Repeat the complete dry-run against the exact re-uploaded file immediately
    # before writing. This prevents a changed file or destination from bypassing
    # the discrepancy/empty-target safeguards.
    uploaded_file.seek(0)
    report = analyze_legacy_sqlite(uploaded_file, target_business, source_business_id)
    if not report["ready"]:
        raise LegacyImportError("Import blocked: " + " ".join(report["blockers"]))
    uploaded_file.seek(0)

    with LegacySQLite(uploaded_file) as db, transaction.atomic():
        ordered, infos = _build_source_infos(db, int(source_business_id))
        profile_fields = _copy_business_profile(db, int(source_business_id), target_business)
        user_map, role_map, membership_map, identity_stats = _import_identity(
            db, int(source_business_id), target_business
        )
        mappings = {model: {} for model in ordered}
        imported_counts = {}
        deferred_updates = []

        for model in ordered:
            info = infos[model]
            if not info.source_ids or not info.columns:
                imported_counts[_model_label(model)] = 0
                continue
            rows = _source_table_rows(db, model, info)
            label = _model_label(model)
            manager = getattr(model, "raw_objects", model._base_manager)

            # Business-wide singleton/config rows may have been lazily created
            # on the new tenant. Update that one row rather than duplicating it.
            existing_singleton = None
            if label in SINGLETON_LABELS:
                business_field = _field_to_business(model)
                existing_singleton = manager.filter(**{business_field.name: target_business}).first()

            created_objects = []
            created_source_ids = []
            for index, row in enumerate(rows):
                kwargs, deferred = _transform_row(
                    model, row, target_business=target_business, mappings=mappings,
                    user_map=user_map, role_map=role_map, membership_map=membership_map,
                )
                source_pk = row[model._meta.pk.column]
                if existing_singleton is not None and index == 0:
                    writable = []
                    for key, value in kwargs.items():
                        if key.endswith("_id"):
                            field_name = key[:-3]
                            setattr(existing_singleton, key, value)
                            writable.append(field_name)
                        elif key not in {"business"}:
                            setattr(existing_singleton, key, value)
                            writable.append(key)
                    if writable:
                        existing_singleton.save(update_fields=sorted(set(writable + ["updated_at"] if hasattr(existing_singleton, "updated_at") else writable)))
                    mappings[model][source_pk] = existing_singleton.pk
                    for field_name, related_model, old_fk in deferred:
                        deferred_updates.append((model, existing_singleton.pk, field_name, related_model, old_fk))
                    continue
                obj = model(**kwargs)
                created_objects.append(obj)
                created_source_ids.append(source_pk)
                for field_name, related_model, old_fk in deferred:
                    deferred_updates.append((model, obj, field_name, related_model, old_fk))

            if created_objects:
                manager.bulk_create(created_objects, batch_size=250)
                for source_pk, obj in zip(created_source_ids, created_objects):
                    mappings[model][source_pk] = obj.pk
            imported_counts[label] = len(rows)

        # Nullable circular/deferred relations are repaired in bounded batches.
        grouped = defaultdict(list)
        for model, obj_ref, field_name, related_model, old_fk in deferred_updates:
            obj_pk = obj_ref.pk if hasattr(obj_ref, "pk") else obj_ref
            mapped_fk = mappings.get(related_model, {}).get(old_fk)
            if mapped_fk is None:
                continue
            grouped[(model, field_name)].append((obj_pk, mapped_fk))
        for (model, field_name), pairs in grouped.items():
            field = model._meta.get_field(field_name)
            manager = getattr(model, "raw_objects", model._base_manager)
            objects = list(manager.filter(pk__in=[pk for pk, _ in pairs]))
            value_by_pk = dict(pairs)
            for obj in objects:
                setattr(obj, field.attname, value_by_pk[obj.pk])
            manager.bulk_update(objects, [field_name], batch_size=250)

        total_rows = sum(imported_counts.values())
        AuditLog.raw_objects.create(
            business=target_business,
            created_by=actor if getattr(actor, "pk", None) else None,
            action="legacy_import",
            model_name="Business",
            object_id=str(target_business.pk),
            description=f"Founder imported legacy SQLite data into {target_business.name}.",
            metadata={
                "source_business_id": int(source_business_id),
                "operational_rows": total_rows,
                "users_created": identity_stats["users_created"],
                "users_matched": identity_stats["users_matched"],
                "memberships": identity_stats["memberships"],
            },
        )
        return {
            "source_business": report["source_business"],
            "target_business": target_business,
            "profile_fields": profile_fields,
            "identity": identity_stats,
            "models": imported_counts,
            "total_rows": total_rows,
        }

# ---------------------------------------------------------------------------
# Django JSON tenant-backup support
# ---------------------------------------------------------------------------

class LegacyJSONFixture:
    """Read the JSON produced by INPROFIC's Reports -> Backup action.

    Older versions accidentally serialized several tenants into one fixture.
    The importer therefore never trusts the file boundary: it re-establishes
    ownership from Business FKs and owning parent relations before analysis or
    import.
    """

    def __init__(self, uploaded_file):
        self.uploaded_file = uploaded_file
        self.records = []
        self.by_label = defaultdict(dict)

    def __enter__(self):
        if getattr(self.uploaded_file, "size", 0) > MAX_JSON_BYTES:
            raise LegacyImportError("JSON backup is larger than the 25 MB Founder Console safety limit. Use the raw SQLite import path for unusually large legacy datasets.")
        try:
            self.uploaded_file.seek(0)
            raw = b"".join(self.uploaded_file.chunks())
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LegacyImportError("The uploaded JSON backup is not a valid Django fixture.") from exc
        finally:
            try:
                self.uploaded_file.seek(0)
            except Exception:
                pass
        if not isinstance(payload, list):
            raise LegacyImportError("The uploaded JSON backup must contain a Django fixture list.")
        for index, record in enumerate(payload, start=1):
            if not isinstance(record, dict) or not isinstance(record.get("fields"), dict):
                raise LegacyImportError(f"JSON fixture entry #{index} is malformed.")
            label = str(record.get("model") or "").strip().lower()
            pk = record.get("pk")
            if not label or pk is None:
                raise LegacyImportError(f"JSON fixture entry #{index} has no model label or primary key.")
            if pk in self.by_label[label]:
                raise LegacyImportError(f"JSON fixture contains duplicate {label} primary key {pk}.")
            normalized = {"model": label, "pk": pk, "fields": record["fields"]}
            self.records.append(normalized)
            self.by_label[label][pk] = normalized
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.uploaded_file.seek(0)
        except Exception:
            pass

    def businesses(self):
        rows = []
        for record in self.by_label.get("core.business", {}).values():
            fields = record["fields"]
            rows.append({
                "id": record["pk"],
                "name": fields.get("name", ""),
                "slug": fields.get("slug", ""),
                "vertical": fields.get("vertical", ""),
            })
        return sorted(rows, key=lambda row: int(row["id"]))


def _fixture_model(label):
    try:
        app_label, model_name = label.split(".", 1)
    except ValueError:
        return None
    if app_label not in TENANT_APPS:
        return None
    try:
        return apps.get_model(app_label, model_name)
    except LookupError:
        return None


def _fixture_scope(fixture: LegacyJSONFixture, source_business_id):
    """Return records proven to belong to exactly one source tenant."""
    business_records = fixture.by_label.get("core.business", {})
    source_record = business_records.get(source_business_id)
    if source_record is None:
        # JSON integer keys stay integers, but accept a numeric string from the form.
        source_record = next(
            (record for key, record in business_records.items() if str(key) == str(source_business_id)),
            None,
        )
    if source_record is None:
        raise LegacyImportError("The selected source tenant ID does not exist in this JSON backup.")
    source_business_id = source_record["pk"]

    scoped = defaultdict(dict)
    scoped["core.business"][source_business_id] = source_record

    recognized = {}
    for label in fixture.by_label:
        model = _fixture_model(label)
        if model is not None:
            recognized[label] = model

    # First select every model that carries its own Business FK.
    for label, model in recognized.items():
        if model is Business:
            continue
        business_field = _field_to_business(model)
        if not business_field:
            continue
        for pk, record in fixture.by_label[label].items():
            if str(record["fields"].get(business_field.name)) == str(source_business_id):
                scoped[label][pk] = record

    # Then close over child tables such as RecipeItem, OrderItem and SaleItem.
    # A child is admitted only when at least one owning tenant parent is already
    # selected and no referenced tenant parent present in the file belongs to a
    # different tenant.
    changed = True
    while changed:
        changed = False
        for label, model in recognized.items():
            if model is Business or _field_to_business(model):
                continue
            for pk, record in fixture.by_label[label].items():
                if pk in scoped[label]:
                    continue
                anchored = False
                foreign_parent = False
                for field in model._meta.concrete_fields:
                    if not field.is_relation or field.related_model is get_user_model():
                        continue
                    related_label = field.related_model._meta.label_lower
                    if related_label not in recognized and related_label != "core.business":
                        continue
                    old_fk = record["fields"].get(field.name)
                    if old_fk is None:
                        continue
                    all_related = fixture.by_label.get(related_label, {})
                    if old_fk not in all_related:
                        # The old exporter omitted some parent model families;
                        # a different relation can still prove tenant ownership.
                        continue
                    if old_fk in scoped.get(related_label, {}):
                        anchored = True
                    else:
                        foreign_parent = True
                if anchored and not foreign_parent:
                    scoped[label][pk] = record
                    changed = True

    return source_record, scoped


def _fixture_required_missing_fields(model, records):
    if not records:
        return []
    available = set().union(*(set(record["fields"]) for record in records))
    missing = []
    for field in model._meta.concrete_fields:
        if field.primary_key or field.auto_created or getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False):
            continue
        if isinstance(field, (models.FileField, models.ImageField)):
            continue
        if field.is_relation and field.related_model is Business:
            continue
        if field.name in available:
            continue
        if field.null or field.has_default():
            continue
        # Old tenant JSON backups intentionally did not include user identity.
        if field.is_relation and field.related_model is get_user_model() and field.null:
            continue
        missing.append(field.name)
    return missing


def _fixture_customer_specs(scoped):
    """Reconstruct customer masters omitted by the historical JSON exporter."""
    included = set(scoped.get("sales.customer", {}))
    specs = defaultdict(lambda: {"names": set(), "regions": set(), "groups": set()})
    for record in scoped.get("production.order", {}).values():
        fields = record["fields"]
        customer_id = fields.get("customer")
        if customer_id is None or customer_id in included:
            continue
        if fields.get("customer_name"):
            specs[customer_id]["names"].add(str(fields["customer_name"]).strip())
        if fields.get("customer_region"):
            specs[customer_id]["regions"].add(str(fields["customer_region"]).strip())
        if fields.get("customer_group"):
            specs[customer_id]["groups"].add(str(fields["customer_group"]).strip())
    for record in scoped.get("sales.sale", {}).values():
        fields = record["fields"]
        customer_id = fields.get("customer_master")
        if customer_id is None or customer_id in included:
            continue
        if fields.get("customer"):
            specs[customer_id]["names"].add(str(fields["customer"]).strip())
    return specs


def _fixture_global_unique_conflicts(scoped, ordered):
    conflicts = []
    for model in ordered:
        label = _model_label(model)
        records = list(scoped.get(label, {}).values())
        if not records:
            continue
        manager = getattr(model, "raw_objects", model._base_manager)
        for field in model._meta.concrete_fields:
            if not field.unique or field.primary_key or field.is_relation or isinstance(field, (models.FileField, models.ImageField)):
                continue
            values = []
            seen = set()
            for record in records:
                raw = record["fields"].get(field.name)
                if raw in (None, ""):
                    continue
                try:
                    value = field.to_python(raw)
                    hash(value)
                except Exception:
                    continue
                if value not in seen:
                    seen.add(value)
                    values.append(value)
            count = 0
            for start in range(0, len(values), 500):
                count += manager.filter(**{f"{field.name}__in": values[start:start + 500]}).count()
            if count:
                conflicts.append({
                    "model": model._meta.verbose_name_plural.title(),
                    "label": label,
                    "field": field.name,
                    "count": count,
                })
    return conflicts


def analyze_legacy_fixture(uploaded_file, target_business, source_business_id=None):
    with LegacyJSONFixture(uploaded_file) as fixture:
        businesses = fixture.businesses()
        selected = None
        if source_business_id is not None:
            selected = next((row for row in businesses if str(row["id"]) == str(source_business_id)), None)
            if selected is None:
                raise LegacyImportError("The selected source tenant ID does not exist in this JSON backup.")
        elif len(businesses) == 1:
            selected = businesses[0]
            source_business_id = selected["id"]

        report = {
            "backup_format": "Django JSON tenant backup",
            "source_businesses": businesses,
            "source_business": selected,
            "source_business_id": source_business_id,
            "target_business": target_business,
            "models": [], "blockers": [], "warnings": [], "ready": False,
            "unique_conflicts": [], "target_conflicts": [],
        }
        if selected is None:
            report["blockers"].append(
                "This legacy backup contains multiple tenants. Select the source tenant ID shown below; all other tenant rows will be ignored."
            )
            return report

        source_record, scoped = _fixture_scope(fixture, selected["id"])
        selected["id"] = source_record["pk"]
        source_business_id = source_record["pk"]
        report["source_business_id"] = source_business_id

        ignored_rows = sum(
            len(records) - len(scoped.get(label, {}))
            for label, records in fixture.by_label.items()
            if label != "core.business"
        )
        other_businesses = max(0, len(businesses) - 1)
        if other_businesses or ignored_rows:
            report["warnings"].append(
                f"This was produced by the older unscoped backup exporter. {other_businesses} other tenant record(s) and {ignored_rows} non-selected operational row(s) are excluded from this import."
            )

        present_models = []
        for label, records in scoped.items():
            if label in {"core.business", "core.auditlog"} or label in EXCLUDED_LABELS or not records:
                continue
            model = _fixture_model(label)
            if model is not None:
                present_models.append(model)
        ordered = _dependency_order(set(present_models)) if present_models else []

        for model in ordered:
            label = _model_label(model)
            records = list(scoped[label].values())
            missing = _fixture_required_missing_fields(model, records)
            validation_errors = []
            for record in records:
                for field in model._meta.concrete_fields:
                    if (
                        field.primary_key or field.auto_created or getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False)
                        or field.is_relation or isinstance(field, (models.FileField, models.ImageField))
                        or field.name not in record["fields"]
                    ):
                        continue
                    try:
                        field.clean(record["fields"].get(field.name), None)
                    except Exception as exc:
                        validation_errors.append(f"row {record['pk']} · {field.name}: {exc}")
                        if len(validation_errors) >= 8:
                            break
                if len(validation_errors) >= 8:
                    break
            if missing:
                report["blockers"].append(
                    f"{model._meta.verbose_name_plural.title()}: backup is missing required current field(s): {', '.join(missing)}."
                )
            if validation_errors:
                report["blockers"].append(
                    f"{model._meta.verbose_name_plural.title()}: backup values do not satisfy the current schema: "
                    + " | ".join(validation_errors)
                )
            report["models"].append({
                "label": label, "name": model._meta.verbose_name_plural.title(),
                "rows": len(records), "table_present": True,
                "missing_required": missing, "ignored_source_columns": [],
                "skipped_file_fields": [], "validation_errors": validation_errors,
            })

        audit_count = len(scoped.get("core.auditlog", {}))
        if audit_count:
            report["warnings"].append(
                f"{audit_count} legacy audit-log row(s) will not be copied because their object IDs refer to old database primary keys; a new Founder import audit record will be created instead."
            )

        User = get_user_model()
        user_refs = set()
        for label, records in scoped.items():
            model = _fixture_model(label)
            if model is None:
                continue
            for field in model._meta.concrete_fields:
                if field.is_relation and field.related_model is User:
                    user_refs.update(
                        record["fields"].get(field.name)
                        for record in records.values()
                        if record["fields"].get(field.name) is not None
                    )
        if user_refs:
            report["warnings"].append(
                f"The historical JSON exporter did not include user identity rows. Creator/reversal attribution referencing {len(user_refs)} legacy user ID(s) will be cleared rather than guessed."
            )

        customer_specs = _fixture_customer_specs(scoped)
        if customer_specs:
            report["warnings"].append(
                f"The historical exporter omitted {len(customer_specs)} referenced customer master record(s). They will be reconstructed from the order/sale customer-name snapshots before dependent rows are imported."
            )
            reconstructed_names = {}
            for old_id, info in customer_specs.items():
                names = sorted(name for name in info["names"] if name)
                chosen = names[0] if names else f"Legacy customer #{old_id}"
                key = chosen.casefold()
                if key in reconstructed_names and reconstructed_names[key] != old_id:
                    report["blockers"].append(
                        "Two omitted legacy customer IDs resolve to the same customer name. Resolve that ambiguity before importing."
                    )
                    break
                reconstructed_names[key] = old_id

        location_refs = {
            record["fields"].get("location")
            for label in ("inventory.stockmovement", "inventory.stockadjustment", "inventory.operationalsupplydispense")
            for record in scoped.get(label, {}).values()
            if record["fields"].get("location") is not None
        }
        included_locations = set(scoped.get("inventory.inventorylocation", {}))
        missing_locations = {pk for pk in location_refs if pk not in included_locations}
        if missing_locations:
            report["warnings"].append(
                f"The historical exporter omitted InventoryLocation row(s) referenced by {len(missing_locations)} legacy location ID(s). Those references will map to the destination tenant's Main Store."
            )

        batch_refs = {
            record["fields"].get("production_batch")
            for record in scoped.get("sales.saleitem", {}).values()
            if record["fields"].get("production_batch") is not None
        }
        included_batches = set(scoped.get("production.productionbatch", {}))
        missing_batches = {pk for pk in batch_refs if pk not in included_batches}
        if missing_batches:
            report["warnings"].append(
                f"The old export omitted {len(missing_batches)} referenced ProductionBatch ID(s). Historical sales and unit-cost snapshots will be preserved, but those nullable traceability links will remain empty rather than being fabricated."
            )

        conflicts = _target_conflicts(target_business, ordered)
        # A bootstrapped Main Store is expected and can be reused safely.
        conflicts = [row for row in conflicts if row["label"] != "inventory.inventorylocation"]
        from sales.models import Customer
        if customer_specs:
            existing_customers = Customer.raw_objects.filter(business=target_business).count()
            if existing_customers:
                conflicts.append({"model": "Customers", "label": "sales.customer", "count": existing_customers})
        if conflicts:
            report["blockers"].append(
                "Destination tenant already contains operational data. Import into a fresh/empty tenant to prevent duplicate or ambiguous merges."
            )
            report["target_conflicts"] = conflicts

        unique_conflicts = _fixture_global_unique_conflicts(scoped, ordered)
        if unique_conflicts:
            report["blockers"].append(
                "The legacy tenant contains globally unique value(s) that already exist elsewhere in the live database. Resolve these collisions before importing."
            )
            report["unique_conflicts"] = unique_conflicts

        report["ready"] = not report["blockers"]
        return report


def _fixture_scalar(field, value):
    if value is None:
        return None
    try:
        return field.to_python(value)
    except Exception:
        return value


def _copy_fixture_business_profile(source_record, target_business):
    updated = []
    fields = source_record["fields"]
    for field_name in BUSINESS_PROFILE_FIELDS:
        if field_name in fields and fields[field_name] is not None:
            setattr(target_business, field_name, fields[field_name])
            updated.append(field_name)
    if updated:
        target_business.save(update_fields=updated)
    return updated


def _reconstruct_fixture_customers(scoped, target_business):
    from sales.models import Customer
    specs = _fixture_customer_specs(scoped)
    mapping = {}
    to_create = []
    source_ids = []
    for old_id, info in sorted(specs.items(), key=lambda item: str(item[0])):
        names = sorted(name for name in info["names"] if name)
        regions = sorted(region for region in info["regions"] if region)
        groups = sorted(group for group in info["groups"] if group)
        name = names[0] if names else f"Legacy customer #{old_id}"
        to_create.append(Customer(
            business=target_business,
            created_by=None,
            name=name[:160],
            region=(regions[0] if regions else "")[:100],
            customer_group=(groups[0] if groups else "")[:100],
        ))
        source_ids.append(old_id)
    if to_create:
        Customer.raw_objects.bulk_create(to_create, batch_size=100)
        for old_id, obj in zip(source_ids, to_create):
            mapping[old_id] = obj.pk
    return mapping


def _transform_fixture_record(model, record, *, target_business, mappings, customer_map, main_location_id):
    kwargs = {}
    deferred = []
    User = get_user_model()
    fields = record["fields"]
    from sales.models import Customer
    from inventory.models import InventoryLocation
    for field in model._meta.concrete_fields:
        if field.primary_key or field.auto_created or getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False):
            continue
        if isinstance(field, (models.FileField, models.ImageField)):
            continue
        if field.is_relation:
            old_fk = fields.get(field.name)
            if field.related_model is Business:
                kwargs[field.attname] = target_business.pk
            elif field.related_model is User:
                if old_fk is not None and not field.null:
                    raise LegacyImportError(f"{model._meta.label}: required legacy user reference {old_fk} cannot be restored from this JSON backup.")
                kwargs[field.attname] = None
            elif field.related_model is Customer and old_fk in customer_map:
                kwargs[field.attname] = customer_map[old_fk]
            elif field.related_model is InventoryLocation and old_fk is not None and field.related_model not in mappings:
                kwargs[field.attname] = main_location_id
            elif field.related_model in mappings:
                mapped = mappings[field.related_model].get(old_fk)
                if mapped is None and old_fk is not None:
                    if field.null:
                        kwargs[field.attname] = None
                        deferred.append((field.name, field.related_model, old_fk))
                    else:
                        raise LegacyImportError(
                            f"{model._meta.label}: required {field.name} reference {old_fk} is absent from the selected tenant backup."
                        )
                else:
                    kwargs[field.attname] = mapped
            elif old_fk is None or field.null:
                kwargs[field.attname] = None
            else:
                raise LegacyImportError(
                    f"{model._meta.label}: required relation {field.name} ({old_fk}) is not present in the selected tenant backup."
                )
            continue
        if field.name in fields:
            kwargs[field.name] = _fixture_scalar(field, fields.get(field.name))
    return kwargs, deferred


def import_legacy_fixture(uploaded_file, target_business, source_business_id, *, actor=None):
    uploaded_file.seek(0)
    report = analyze_legacy_fixture(uploaded_file, target_business, source_business_id)
    if not report["ready"]:
        raise LegacyImportError("Import blocked: " + " ".join(report["blockers"]))
    uploaded_file.seek(0)

    with LegacyJSONFixture(uploaded_file) as fixture, transaction.atomic():
        source_record, scoped = _fixture_scope(fixture, source_business_id)
        present_models = []
        for label, records in scoped.items():
            if label in {"core.business", "core.auditlog"} or label in EXCLUDED_LABELS or not records:
                continue
            model = _fixture_model(label)
            if model is not None:
                present_models.append(model)
        ordered = _dependency_order(set(present_models)) if present_models else []
        profile_fields = _copy_fixture_business_profile(source_record, target_business)

        from inventory.models import InventoryLocation
        main_location, _ = InventoryLocation.raw_objects.get_or_create(
            business=target_business,
            name="Main Store",
            defaults={"location_type": "store", "active": True, "created_by": None},
        )
        customer_map = _reconstruct_fixture_customers(scoped, target_business)

        mappings = {model: {} for model in ordered}
        imported_counts = {}
        deferred_updates = []
        from inventory.models import InventoryLocation

        for model in ordered:
            label = _model_label(model)
            records = sorted(scoped.get(label, {}).values(), key=lambda record: str(record["pk"]))
            manager = getattr(model, "raw_objects", model._base_manager)
            imported_counts[label] = len(records)
            if not records:
                continue

            # Reuse bootstrapped Main Store and other same-name locations.
            if model is InventoryLocation:
                existing_by_name = {
                    obj.name: obj for obj in InventoryLocation.raw_objects.filter(business=target_business)
                }
                pending = []
                pending_ids = []
                for record in records:
                    name = str(record["fields"].get("name") or "Main Store")[:80]
                    existing = existing_by_name.get(name)
                    if existing:
                        mappings[model][record["pk"]] = existing.pk
                        continue
                    kwargs, _ = _transform_fixture_record(
                        model, record, target_business=target_business, mappings=mappings,
                        customer_map=customer_map, main_location_id=main_location.pk,
                    )
                    pending.append(model(**kwargs)); pending_ids.append(record["pk"])
                if pending:
                    manager.bulk_create(pending, batch_size=100)
                    for old_pk, obj in zip(pending_ids, pending):
                        mappings[model][old_pk] = obj.pk
                continue

            existing_singleton = None
            if label in SINGLETON_LABELS:
                business_field = _field_to_business(model)
                existing_singleton = manager.filter(**{business_field.name: target_business}).first()

            created = []
            source_ids = []
            local_deferred = []
            for index, record in enumerate(records):
                kwargs, deferred = _transform_fixture_record(
                    model, record, target_business=target_business, mappings=mappings,
                    customer_map=customer_map, main_location_id=main_location.pk,
                )
                old_pk = record["pk"]
                if existing_singleton is not None and index == 0:
                    writable = []
                    for key, value in kwargs.items():
                        if key in {"business", "business_id"}:
                            continue
                        setattr(existing_singleton, key, value)
                        writable.append(key[:-3] if key.endswith("_id") else key)
                    if writable:
                        existing_singleton.save(update_fields=sorted(set(writable)))
                    mappings[model][old_pk] = existing_singleton.pk
                    for field_name, related_model, old_fk in deferred:
                        deferred_updates.append((model, existing_singleton.pk, field_name, related_model, old_fk))
                    continue
                obj = model(**kwargs)
                created.append(obj); source_ids.append(old_pk); local_deferred.append((obj, deferred))
            if created:
                manager.bulk_create(created, batch_size=250)
                for old_pk, obj, (_, deferred) in zip(source_ids, created, local_deferred):
                    mappings[model][old_pk] = obj.pk
                    for field_name, related_model, old_fk in deferred:
                        deferred_updates.append((model, obj.pk, field_name, related_model, old_fk))

        grouped = defaultdict(list)
        for model, obj_pk, field_name, related_model, old_fk in deferred_updates:
            mapped_fk = mappings.get(related_model, {}).get(old_fk)
            if mapped_fk is not None:
                grouped[(model, field_name)].append((obj_pk, mapped_fk))
        for (model, field_name), pairs in grouped.items():
            field = model._meta.get_field(field_name)
            manager = getattr(model, "raw_objects", model._base_manager)
            objects = list(manager.filter(pk__in=[pk for pk, _ in pairs]))
            values = dict(pairs)
            for obj in objects:
                setattr(obj, field.attname, values[obj.pk])
            manager.bulk_update(objects, [field_name], batch_size=250)

        total_rows = sum(imported_counts.values())
        identity_stats = {"users_created": 0, "users_matched": 0, "memberships": 0}
        AuditLog.raw_objects.create(
            business=target_business,
            created_by=actor if getattr(actor, "pk", None) else None,
            action="legacy_import",
            model_name="Business",
            object_id=str(target_business.pk),
            description=f"Founder imported legacy JSON tenant backup into {target_business.name}.",
            metadata={
                "source_business_id": int(source_business_id),
                "backup_format": "django_json",
                "operational_rows": total_rows,
                "reconstructed_customers": len(customer_map),
            },
        )
        return {
            "source_business": report["source_business"], "target_business": target_business,
            "profile_fields": profile_fields, "identity": identity_stats,
            "models": imported_counts, "total_rows": total_rows,
            "reconstructed_customers": len(customer_map),
        }


def detect_legacy_backup_format(uploaded_file):
    try:
        uploaded_file.seek(0)
        head = uploaded_file.read(64)
    finally:
        try:
            uploaded_file.seek(0)
        except Exception:
            pass
    if head.startswith(b"SQLite format 3\x00"):
        return "sqlite"
    if head.lstrip().startswith((b"[", b"{")):
        return "json"
    raise LegacyImportError("Unsupported backup format. Upload an INPROFIC JSON backup or a SQLite .db/.sqlite3 backup.")


def analyze_legacy_backup(uploaded_file, target_business, source_business_id=None):
    backup_format = detect_legacy_backup_format(uploaded_file)
    if backup_format == "json":
        return analyze_legacy_fixture(uploaded_file, target_business, source_business_id)
    report = analyze_legacy_sqlite(uploaded_file, target_business, source_business_id)
    report["backup_format"] = "SQLite database"
    return report


def import_legacy_backup(uploaded_file, target_business, source_business_id, *, actor=None):
    backup_format = detect_legacy_backup_format(uploaded_file)
    if backup_format == "json":
        return import_legacy_fixture(uploaded_file, target_business, source_business_id, actor=actor)
    return import_legacy_sqlite(uploaded_file, target_business, source_business_id, actor=actor)
