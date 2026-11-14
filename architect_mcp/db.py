"""Durable database persistence for Architect-MCP build plans & Shop Steward safety gates.

Supports standalone SQLite (default), PostgreSQL (`DATABASE_URL`), and an optional
companion campus web app bridge (`ARCHITECT_WEBAPP_DATABASE_URL`) so build plans
and safety sign-off requests created in a student's personal AI agent appear
directly in the campus Shop Steward portal.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, select

from architect_mcp.models import BOMInput, BuildPlanSpec, StepInput, Subsystem


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _create_db_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine with SQLite thread-safety handling."""
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


# =============================================================================
# Portable Architect-MCP SQLModel Tables
# =============================================================================


class WorkflowProjectRow(SQLModel, table=True):
    """Persisted hardware build plan in `bucket://workflows`."""

    __tablename__ = "architect_projects"

    id: Optional[int] = Field(default=None, primary_key=True)
    university_slug: str = Field(index=True, max_length=120)
    student_id: str = Field(index=True, max_length=255)
    title: str = Field(max_length=255)
    description: str = Field(default="")
    status: str = Field(default="ACTIVE", index=True, max_length=40)
    webapp_project_id: Optional[int] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class WorkflowStepRow(SQLModel, table=True):
    """Persisted build step with deterministic Shop Steward safety gate state."""

    __tablename__ = "architect_steps"

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="architect_projects.id", index=True)
    step_order: int = Field(ge=1)
    subsystem: str = Field(default=Subsystem.ASSEMBLY.value, max_length=64)
    title: str = Field(max_length=255)
    instructions: str
    area: Optional[str] = Field(default=None, max_length=120)
    machines_used_json: str = Field(default="[]")
    requires_steward_signoff: bool = Field(default=False, index=True)
    signoff_status: str = Field(default="NONE", index=True, max_length=40)
    safety_notes: Optional[str] = Field(default=None)
    is_completed: bool = Field(default=False)
    safety_approved_by: Optional[str] = Field(default=None, max_length=255)
    safety_approved_at: Optional[datetime] = Field(default=None)


class WorkflowBOMRow(SQLModel, table=True):
    """Persisted Bill of Materials line item for a build plan."""

    __tablename__ = "architect_bom_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="architect_projects.id", index=True)
    part_name: str = Field(max_length=255)
    quantity_needed: int = Field(default=1, ge=1)
    matched_sku: Optional[str] = Field(default=None, max_length=120)
    in_stock_on_campus: bool = Field(default=False)
    location_hint: Optional[str] = Field(default=None, max_length=255)
    external_purchase_note: Optional[str] = Field(default=None)


# =============================================================================
# Optional Companion Campus Web App Bridge
# =============================================================================

_SUBSYSTEM_WEB_MAP = {
    Subsystem.MECHANICAL.value: "mechanical",
    Subsystem.ELECTRICAL.value: "electrical",
    Subsystem.FIRMWARE.value: "firmware",
    Subsystem.ASSEMBLY.value: "assembly",
    Subsystem.FABRICATION.value: "mechanical",
    Subsystem.TEXTILES.value: "aesthetic",
    Subsystem.GENERAL.value: "assembly",
}


class WebAppBridge:
    """Mirrors MCP build plans and steward safety gates to a companion campus web DB.

    Activates only when the target database contains the companion web portal schema
    (`users`, `projects`, `project_steps`, `project_bom_items`). Never hardcodes any
    specific university identity.
    """

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.engine = _create_db_engine(database_url)

    def has_compatible_schema(self) -> bool:
        """Check whether the companion web portal tables exist in the target database."""
        inspector = inspect(self.engine)
        tables = set(inspector.get_table_names())
        required = {"users", "projects", "project_steps", "project_bom_items"}
        return required.issubset(tables)

    def _resolve_or_create_user(self, conn: Any, identifier: str, role: str = "student") -> int:
        email = identifier if "@" in identifier else f"{identifier}@campus.local"
        name = identifier.split("@")[0].replace(".", " ").replace("_", " ").title() or "Maker"

        row = conn.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": email},
        ).fetchone()
        if row:
            return int(row[0])

        now = _utcnow()
        conn.execute(
            text(
                "INSERT INTO users (name, email, role, created_at) "
                "VALUES (:name, :email, :role, :created_at)"
            ),
            {"name": name, "email": email, "role": role, "created_at": now},
        )
        created = conn.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": email},
        ).fetchone()
        return int(created[0])

    def sync_plan(
        self,
        plan: BuildPlanSpec,
        existing_webapp_project_id: Optional[int] = None,
    ) -> Optional[int]:
        """Mirror a build plan and its steps/BOM into the companion web app database."""
        if not self.has_compatible_schema():
            return None

        now = _utcnow()
        with self.engine.begin() as conn:
            student_pk = self._resolve_or_create_user(conn, plan.student_id, role="student")

            if existing_webapp_project_id is not None:
                project_pk = existing_webapp_project_id
                conn.execute(
                    text(
                        "UPDATE projects SET title = :title, description = :description, "
                        "updated_at = :updated_at WHERE id = :pid"
                    ),
                    {
                        "title": plan.title,
                        "description": plan.description or plan.title,
                        "updated_at": now,
                        "pid": project_pk,
                    },
                )
                conn.execute(
                    text("DELETE FROM project_steps WHERE project_id = :pid"),
                    {"pid": project_pk},
                )
                conn.execute(
                    text("DELETE FROM project_bom_items WHERE project_id = :pid"),
                    {"pid": project_pk},
                )
            else:
                status_val = (
                    "completed"
                    if plan.steps and all(s.is_completed for s in plan.steps)
                    else "in_progress"
                )
                result = conn.execute(
                    text(
                        "INSERT INTO projects (student_id, title, description, status, created_at, updated_at) "
                        "VALUES (:student_id, :title, :description, :status, :created_at, :updated_at)"
                    ),
                    {
                        "student_id": student_pk,
                        "title": plan.title,
                        "description": plan.description or plan.title,
                        "status": status_val,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                project_pk = int(result.lastrowid)

            for step in plan.steps:
                approver_pk = (
                    self._resolve_or_create_user(conn, step.safety_approved_by, role="steward")
                    if step.safety_approved_by
                    else None
                )
                safety_level = (
                    "steward_sign_off_required"
                    if step.requires_steward_signoff
                    else "normal"
                )
                subsystem_val = _SUBSYSTEM_WEB_MAP.get(step.subsystem.value, "assembly")
                conn.execute(
                    text(
                        "INSERT INTO project_steps "
                        "(project_id, step_order, subsystem, title, instructions, facility, "
                        "safety_level, requires_steward_signoff, safety_notes, is_completed, "
                        "safety_approved_by, safety_approved_at) "
                        "VALUES (:project_id, :step_order, :subsystem, :title, :instructions, :facility, "
                        ":safety_level, :requires_steward_signoff, :safety_notes, :is_completed, "
                        ":safety_approved_by, :safety_approved_at)"
                    ),
                    {
                        "project_id": project_pk,
                        "step_order": step.step_order,
                        "subsystem": subsystem_val,
                        "title": step.title,
                        "instructions": step.instructions,
                        "facility": None,
                        "safety_level": safety_level,
                        "requires_steward_signoff": step.requires_steward_signoff,
                        "safety_notes": step.safety_notes,
                        "is_completed": step.is_completed,
                        "safety_approved_by": approver_pk,
                        "safety_approved_at": step.safety_approved_at,
                    },
                )

            for bom in plan.bom_items:
                source_status = (
                    "in_stock_on_campus"
                    if bom.in_stock_on_campus
                    else "external_purchase_needed"
                )
                conn.execute(
                    text(
                        "INSERT INTO project_bom_items "
                        "(project_id, part_name, quantity_needed, source_status, inventory_item_id, external_purchase_note) "
                        "VALUES (:project_id, :part_name, :quantity_needed, :source_status, :inventory_item_id, :external_purchase_note)"
                    ),
                    {
                        "project_id": project_pk,
                        "part_name": bom.part_name,
                        "quantity_needed": bom.quantity_needed,
                        "source_status": source_status,
                        "inventory_item_id": None,
                        "external_purchase_note": bom.external_purchase_note or bom.location_hint,
                    },
                )

        return project_pk


# =============================================================================
# Durable Workflow Repository
# =============================================================================


class WorkflowRepository:
    """SQLModel-backed repository for `bucket://workflows` and Steward safety gates."""

    def __init__(
        self,
        database_url: str = "sqlite:///./architect_mcp.db",
        webapp_database_url: Optional[str] = None,
    ) -> None:
        self.database_url = database_url
        self.engine = _create_db_engine(database_url)
        self.bridge = WebAppBridge(webapp_database_url) if webapp_database_url else None
        self.init_db()

    def init_db(self) -> None:
        """Create Architect-MCP workflow tables if they do not exist."""
        SQLModel.metadata.create_all(
            self.engine,
            tables=[
                WorkflowProjectRow.__table__,
                WorkflowStepRow.__table__,
                WorkflowBOMRow.__table__,
            ],
        )

    def _hydrate_plan(
        self,
        session: Session,
        proj: WorkflowProjectRow,
    ) -> BuildPlanSpec:
        step_rows = session.exec(
            select(WorkflowStepRow)
            .where(WorkflowStepRow.project_id == proj.id)
            .order_by(WorkflowStepRow.step_order)
        ).all()
        bom_rows = session.exec(
            select(WorkflowBOMRow).where(WorkflowBOMRow.project_id == proj.id)
        ).all()

        steps: list[StepInput] = []
        for s in step_rows:
            try:
                sub = Subsystem(s.subsystem)
            except ValueError:
                sub = Subsystem.ASSEMBLY
            machines = json.loads(s.machines_used_json or "[]")
            steps.append(
                StepInput(
                    step_order=s.step_order,
                    subsystem=sub,
                    title=s.title,
                    instructions=s.instructions,
                    area=s.area,
                    machines_used=machines,
                    requires_steward_signoff=s.requires_steward_signoff,
                    safety_notes=s.safety_notes,
                    is_completed=s.is_completed,
                    safety_approved_by=s.safety_approved_by,
                    safety_approved_at=s.safety_approved_at,
                )
            )

        boms = [
            BOMInput(
                part_name=b.part_name,
                quantity_needed=b.quantity_needed,
                matched_sku=b.matched_sku,
                in_stock_on_campus=b.in_stock_on_campus,
                location_hint=b.location_hint,
                external_purchase_note=b.external_purchase_note,
            )
            for b in bom_rows
        ]

        return BuildPlanSpec(
            project_id=proj.id,
            university_slug=proj.university_slug,
            student_id=proj.student_id,
            title=proj.title,
            description=proj.description,
            steps=steps,
            bom_items=boms,
            created_at=proj.created_at,
        )

    def save_plan(self, plan: BuildPlanSpec) -> BuildPlanSpec:
        """Persist a new or updated BuildPlanSpec and optionally mirror to campus web DB."""
        now = _utcnow()
        with Session(self.engine) as session:
            proj: Optional[WorkflowProjectRow] = None
            if plan.project_id is not None:
                proj = session.get(WorkflowProjectRow, plan.project_id)

            if proj is None:
                proj = WorkflowProjectRow(
                    university_slug=plan.university_slug,
                    student_id=plan.student_id,
                    title=plan.title,
                    description=plan.description,
                    status="ACTIVE",
                    created_at=now,
                    updated_at=now,
                )
                session.add(proj)
                session.commit()
                session.refresh(proj)
            else:
                proj.title = plan.title
                proj.description = plan.description
                proj.updated_at = now
                # Replace child steps & BOM items cleanly
                old_steps = session.exec(
                    select(WorkflowStepRow).where(WorkflowStepRow.project_id == proj.id)
                ).all()
                for os_row in old_steps:
                    session.delete(os_row)
                old_boms = session.exec(
                    select(WorkflowBOMRow).where(WorkflowBOMRow.project_id == proj.id)
                ).all()
                for ob_row in old_boms:
                    session.delete(ob_row)
                session.commit()

            assert proj.id is not None
            for step in plan.steps:
                signoff_status = (
                    "APPROVED"
                    if step.safety_approved_by
                    else ("PENDING_GATE" if step.requires_steward_signoff else "NONE")
                )
                session.add(
                    WorkflowStepRow(
                        project_id=proj.id,
                        step_order=step.step_order,
                        subsystem=step.subsystem.value,
                        title=step.title,
                        instructions=step.instructions,
                        area=step.area,
                        machines_used_json=json.dumps(step.machines_used),
                        requires_steward_signoff=step.requires_steward_signoff,
                        signoff_status=signoff_status,
                        safety_notes=step.safety_notes,
                        is_completed=step.is_completed,
                        safety_approved_by=step.safety_approved_by,
                        safety_approved_at=step.safety_approved_at,
                    )
                )

            for bom in plan.bom_items:
                session.add(
                    WorkflowBOMRow(
                        project_id=proj.id,
                        part_name=bom.part_name,
                        quantity_needed=bom.quantity_needed,
                        matched_sku=bom.matched_sku,
                        in_stock_on_campus=bom.in_stock_on_campus,
                        location_hint=bom.location_hint,
                        external_purchase_note=bom.external_purchase_note,
                    )
                )

            session.commit()
            hydrated = self._hydrate_plan(session, proj)

            if self.bridge is not None:
                webapp_pid = self.bridge.sync_plan(
                    hydrated, existing_webapp_project_id=proj.webapp_project_id
                )
                if webapp_pid is not None and proj.webapp_project_id != webapp_pid:
                    proj.webapp_project_id = webapp_pid
                    session.add(proj)
                    session.commit()

            return hydrated

    def get_plan(self, project_id: int) -> Optional[BuildPlanSpec]:
        """Fetch a persisted build plan by ID."""
        with Session(self.engine) as session:
            proj = session.get(WorkflowProjectRow, project_id)
            if proj is None:
                return None
            return self._hydrate_plan(session, proj)

    def list_plans(
        self,
        student_id: Optional[str] = None,
        university_slug: Optional[str] = None,
    ) -> list[BuildPlanSpec]:
        """List persisted build plans, optionally filtered by student or campus."""
        with Session(self.engine) as session:
            stmt = select(WorkflowProjectRow)
            if student_id:
                stmt = stmt.where(WorkflowProjectRow.student_id == student_id)
            if university_slug:
                stmt = stmt.where(WorkflowProjectRow.university_slug == university_slug)
            stmt = stmt.order_by(WorkflowProjectRow.id)
            projects = session.exec(stmt).all()
            return [self._hydrate_plan(session, p) for p in projects]

    def count_plans(self) -> int:
        with Session(self.engine) as session:
            return len(session.exec(select(WorkflowProjectRow.id)).all())

    def request_signoff(
        self,
        project_id: int,
        step_order: int,
        student_id: str,
        ppe_verified: bool,
        setup_summary: str,
    ) -> dict[str, Any]:
        """Queue a hazardous step for Shop Steward safety sign-off."""
        with Session(self.engine) as session:
            proj = session.get(WorkflowProjectRow, project_id)
            if proj is None:
                return {
                    "status": "ERROR",
                    "message": f"Project ID {project_id} not found in active workflows.",
                }

            step = session.exec(
                select(WorkflowStepRow).where(
                    WorkflowStepRow.project_id == project_id,
                    WorkflowStepRow.step_order == step_order,
                )
            ).first()
            if step is None:
                return {
                    "status": "ERROR",
                    "message": f"Step order {step_order} not found in project {project_id}.",
                }

            if not ppe_verified:
                return {
                    "status": "PPE_NOT_VERIFIED",
                    "requires_action": "Verify safety glasses, tied-back hair, closed-toe shoes, and no loose jewelry before requesting signoff.",
                }

            step.signoff_status = "PENDING_STEWARD_REVIEW"
            step.safety_notes = (
                f"[Steward Review Requested by {student_id}]: {setup_summary} (PPE Verified: Yes)"
            )
            proj.updated_at = _utcnow()
            session.add(step)
            session.add(proj)
            session.commit()

            if self.bridge is not None:
                hydrated = self._hydrate_plan(session, proj)
                self.bridge.sync_plan(hydrated, existing_webapp_project_id=proj.webapp_project_id)

            machines = json.loads(step.machines_used_json or "[]")
            return {
                "status": "PENDING_STEWARD_REVIEW",
                "project_id": project_id,
                "step_order": step_order,
                "step_title": step.title,
                "machines": machines,
                "instructions": "Step queued for Shop Steward review. Please have on-duty Steward inspect your machine setup in person.",
            }

    def approve_signoff(
        self,
        project_id: int,
        step_order: int,
        steward_id: str,
        notes: Optional[str] = None,
    ) -> dict[str, Any]:
        """Record an authorized Shop Steward approval for a hazardous build step."""
        with Session(self.engine) as session:
            proj = session.get(WorkflowProjectRow, project_id)
            if proj is None:
                return {
                    "status": "ERROR",
                    "message": f"Project ID {project_id} not found.",
                }

            step = session.exec(
                select(WorkflowStepRow).where(
                    WorkflowStepRow.project_id == project_id,
                    WorkflowStepRow.step_order == step_order,
                )
            ).first()
            if step is None:
                return {
                    "status": "ERROR",
                    "message": f"Step order {step_order} not found in project {project_id}.",
                }

            now = _utcnow()
            step.signoff_status = "APPROVED"
            step.safety_approved_by = steward_id
            step.safety_approved_at = now
            if notes:
                prefix = f"{step.safety_notes}\n" if step.safety_notes else ""
                step.safety_notes = f"{prefix}[Approved by {steward_id}]: {notes}"
            proj.updated_at = now

            session.add(step)
            session.add(proj)
            session.commit()

            if self.bridge is not None:
                hydrated = self._hydrate_plan(session, proj)
                self.bridge.sync_plan(hydrated, existing_webapp_project_id=proj.webapp_project_id)

            return {
                "status": "APPROVED",
                "project_id": project_id,
                "step_order": step_order,
                "step_title": step.title,
                "approved_by": steward_id,
                "approved_at": now.isoformat(),
            }

    def complete_step(
        self,
        project_id: int,
        step_order: int,
        student_id: str,
        steward_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Complete a build step while enforcing deterministic Shop Steward safety locks."""
        with Session(self.engine) as session:
            proj = session.get(WorkflowProjectRow, project_id)
            if proj is None:
                return {
                    "status": "ERROR",
                    "message": f"Project ID {project_id} not found in active workflows.",
                }

            all_steps = session.exec(
                select(WorkflowStepRow)
                .where(WorkflowStepRow.project_id == project_id)
                .order_by(WorkflowStepRow.step_order)
            ).all()
            target_step = next((s for s in all_steps if s.step_order == step_order), None)

            if target_step is None:
                return {
                    "status": "ERROR",
                    "message": f"Step order {step_order} not found in project {project_id}.",
                }

            if target_step.requires_steward_signoff:
                if not target_step.safety_approved_by and not steward_id:
                    return {
                        "status": "BLOCKED_BY_SAFETY_GATE",
                        "project_id": project_id,
                        "step_order": step_order,
                        "step_title": target_step.title,
                        "error": (
                            f"SAFETY LOCK: Step {step_order} ('{target_step.title}') requires "
                            "in-person Shop Steward sign-off before it can be marked complete. "
                            "Do not power on or operate equipment until a steward inspects your setup."
                        ),
                    }

                if steward_id and not target_step.safety_approved_by:
                    target_step.safety_approved_by = steward_id
                    target_step.safety_approved_at = _utcnow()
                    target_step.signoff_status = "APPROVED"

            target_step.is_completed = True
            now = _utcnow()
            proj.updated_at = now
            all_completed = all(s.is_completed for s in all_steps)
            if all_completed:
                proj.status = "COMPLETED"

            session.add(target_step)
            session.add(proj)
            session.commit()

            if self.bridge is not None:
                hydrated = self._hydrate_plan(session, proj)
                self.bridge.sync_plan(hydrated, existing_webapp_project_id=proj.webapp_project_id)

            return {
                "status": "COMPLETED",
                "project_id": project_id,
                "step_order": step_order,
                "step_title": target_step.title,
                "all_steps_completed": all_completed,
                "approved_by": target_step.safety_approved_by,
                "message": f"Step {step_order} successfully marked complete.",
            }

    def list_pending_signoffs(
        self, university_slug: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """List all hazardous steps awaiting Shop Steward approval."""
        with Session(self.engine) as session:
            stmt = (
                select(WorkflowStepRow, WorkflowProjectRow)
                .join(
                    WorkflowProjectRow,
                    WorkflowStepRow.project_id == WorkflowProjectRow.id,
                )
                .where(
                    WorkflowStepRow.requires_steward_signoff == True,  # noqa: E712
                    WorkflowStepRow.safety_approved_by == None,  # noqa: E711
                )
            )
            if university_slug:
                stmt = stmt.where(WorkflowProjectRow.university_slug == university_slug)

            rows = session.exec(stmt.order_by(WorkflowProjectRow.id, WorkflowStepRow.step_order)).all()
            return [
                {
                    "project_id": proj.id,
                    "project_title": proj.title,
                    "student_id": proj.student_id,
                    "university_slug": proj.university_slug,
                    "step_order": step.step_order,
                    "step_title": step.title,
                    "signoff_status": step.signoff_status,
                    "machines_used": json.loads(step.machines_used_json or "[]"),
                    "safety_notes": step.safety_notes,
                }
                for step, proj in rows
            ]
