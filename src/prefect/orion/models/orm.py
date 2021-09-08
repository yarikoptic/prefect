from typing import List, Union

import pendulum
import sqlalchemy as sa
from sqlalchemy import JSON, Column, ForeignKey, String, join
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship

from prefect.orion.schemas import core, data, schedules, states
from prefect.orion.utilities.database import UUID, Base, Pydantic, Timestamp, now
from prefect.orion.utilities.functions import ParameterSchema


class Flow(Base):
    name = Column(String, nullable=False, unique=True)
    tags = Column(JSON, server_default="[]", default=list, nullable=False)
    parameters = Column(
        Pydantic(ParameterSchema),
        server_default="{}",
        default=ParameterSchema,
        nullable=False,
    )
    flow_runs = relationship("FlowRun", back_populates="flow", lazy="raise")
    deployments = relationship("Deployment", back_populates="flow", lazy="raise")


class FlowRunState(Base):
    # this column isn't explicitly indexed because it is included in
    # the unique compound index on (flow_run_id, timestamp)
    flow_run_id = Column(
        UUID(), ForeignKey("flow_run.id", ondelete="cascade"), nullable=False
    )
    type = Column(sa.Enum(states.StateType), nullable=False, index=True)
    timestamp = Column(
        Timestamp(),
        nullable=False,
        server_default=now(),
        default=lambda: pendulum.now("UTC"),
    )
    name = Column(String, nullable=False)
    message = Column(String)
    state_details = Column(
        Pydantic(states.StateDetails),
        server_default="{}",
        default=states.StateDetails,
        nullable=False,
    )
    run_details = Column(
        Pydantic(states.RunDetails),
        server_default="{}",
        default=states.RunDetails,
        nullable=False,
    )
    data = Column(Pydantic(data.DataDocument), nullable=True)

    flow_run = relationship(
        "FlowRun",
        lazy="raise",
        foreign_keys=[flow_run_id],
    )

    __table_args__ = (
        sa.Index(
            "ix_flow_run_state_flow_run_id_timestamp_desc",
            flow_run_id,
            timestamp.desc(),
            unique=True,
        ),
    )

    def as_state(self) -> states.State:
        return states.State.from_orm(self)


class TaskRunState(Base):
    # this column isn't explicitly indexed because it is included in
    # the unique compound index on (task_run_id, timestamp)
    task_run_id = Column(
        UUID(), ForeignKey("task_run.id", ondelete="cascade"), nullable=False
    )
    type = Column(sa.Enum(states.StateType), nullable=False, index=True)
    timestamp = Column(
        Timestamp(),
        nullable=False,
        server_default=now(),
        default=lambda: pendulum.now("UTC"),
    )
    name = Column(String, nullable=False)
    message = Column(String)
    state_details = Column(
        Pydantic(states.StateDetails),
        server_default="{}",
        default=states.StateDetails,
        nullable=False,
    )
    run_details = Column(
        Pydantic(states.RunDetails),
        server_default="{}",
        default=states.RunDetails,
        nullable=False,
    )
    data = Column(Pydantic(data.DataDocument), nullable=True)

    task_run = relationship(
        "TaskRun",
        lazy="raise",
        foreign_keys=[task_run_id],
    )

    __table_args__ = (
        sa.Index(
            "ix_task_run_state_task_run_id_timestamp_desc",
            task_run_id,
            timestamp.desc(),
            unique=True,
        ),
    )

    def as_state(self) -> states.State:
        return states.State.from_orm(self)


class TaskRunStateCache(Base):
    cache_key = Column(String, nullable=False)
    cache_expiration = Column(
        Timestamp(),
        nullable=True,
    )
    task_run_state_id = Column(UUID(), nullable=False)

    __table_args__ = (
        sa.Index(
            "ix_cache_key_created_desc",
            cache_key,
            sa.desc("created"),
        ),
    )


class FlowRun(Base):
    flow_id = Column(
        UUID(), ForeignKey("flow.id", ondelete="cascade"), nullable=False, index=True
    )
    deployment_id = Column(
        UUID(), ForeignKey("deployment.id", ondelete="set null"), index=True
    )
    flow_version = Column(String)
    parameters = Column(JSON, server_default="{}", default=dict, nullable=False)
    idempotency_key = Column(String)
    context = Column(JSON, server_default="{}", default=dict, nullable=False)
    empirical_policy = Column(JSON, server_default="{}", default={}, nullable=False)
    empirical_config = Column(JSON, server_default="{}", default=dict, nullable=False)
    tags = Column(JSON, server_default="[]", default=list, nullable=False)
    flow_run_details = Column(
        Pydantic(core.FlowRunDetails),
        server_default="{}",
        default=core.FlowRunDetails,
        nullable=False,
    )

    # TODO remove this foreign key for significant delete performance gains
    state_id = Column(
        UUID(),
        ForeignKey(
            "flow_run_state.id",
            ondelete="SET NULL",
            use_alter=True,
        ),
        index=True,
    )
    parent_task_run_id = Column(
        UUID(),
        ForeignKey(
            "task_run.id",
            ondelete="SET NULL",
            use_alter=True,
        ),
        index=True,
    )

    # current states are eagerly loaded unless otherwise specified
    _state = relationship(
        "FlowRunState",
        lazy="joined",
        foreign_keys=[state_id],
        primaryjoin=lambda: FlowRun.state_id == FlowRunState.id,
    )

    @hybrid_property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        """
        If a state is assigned to this run, populate its run id.

        This would normally be handled by the back-populated SQLAlchemy
        relationship, but because this is a one-to-one pointer to a
        one-to-many relationship, SQLAlchemy can't figure it out.
        """
        if value and value.flow_run_id is None:
            value.flow_run_id = self.id
        self._state = value

    flow = relationship("Flow", back_populates="flow_runs", lazy="raise")
    task_runs = relationship(
        "TaskRun",
        back_populates="flow_run",
        lazy="raise",
        foreign_keys=lambda: [TaskRun.flow_run_id],
    )
    parent_task_run = relationship(
        "TaskRun",
        back_populates="subflow_runs",
        lazy="raise",
        foreign_keys=lambda: [FlowRun.parent_task_run_id],
    )

    # unique index on flow id / idempotency key
    __table__args__ = sa.Index(
        "ix_flow_run_flow_id_idempotency_key",
        flow_id,
        idempotency_key,
        unique=True,
    )


class TaskRun(Base):
    flow_run_id = Column(
        UUID(),
        ForeignKey("flow_run.id", ondelete="cascade"),
        nullable=False,
        index=True,
    )
    task_key = Column(String, nullable=False)
    dynamic_key = Column(String)
    cache_key = Column(String)
    cache_expiration = Column(Timestamp())
    task_version = Column(String)
    empirical_policy = Column(
        Pydantic(core.TaskRunPolicy),
        server_default="{}",
        default=core.TaskRunPolicy,
        nullable=False,
    )
    task_inputs = Column(
        Pydantic(ParameterSchema),
        server_default="{}",
        default=ParameterSchema,
        nullable=False,
    )
    tags = Column(JSON, server_default="[]", default=list, nullable=False)

    # TODO remove this foreign key for significant delete performance gains
    state_id = Column(
        UUID(),
        ForeignKey(
            "task_run_state.id",
            ondelete="SET NULL",
            use_alter=True,
        ),
        index=True,
    )
    upstream_task_run_ids = Column(
        JSON, server_default="{}", default=dict, nullable=False
    )
    task_run_details = Column(
        Pydantic(core.TaskRunDetails),
        server_default="{}",
        default=core.TaskRunDetails,
        nullable=False,
    )

    # current states are eagerly loaded unless otherwise specified
    _state = relationship(
        "TaskRunState",
        lazy="joined",
        foreign_keys=[state_id],
        primaryjoin=lambda: TaskRun.state_id == TaskRunState.id,
    )

    @hybrid_property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        """
        If a state is assigned to this run, populate its run id.

        This would normally be handled by the back-populated SQLAlchemy
        relationship, but because this is a one-to-one pointer to a
        one-to-many relationship, SQLAlchemy can't figure it out.
        """
        if value and value.task_run_id is None:
            value.task_run_id = self.id
        self._state = value

    flow_run = relationship(
        FlowRun,
        back_populates="task_runs",
        lazy="raise",
        foreign_keys=[flow_run_id],
    )

    subflow_runs = relationship(
        FlowRun,
        back_populates="parent_task_run",
        lazy="raise",
        foreign_keys=[FlowRun.parent_task_run_id],
    )

    __table_args__ = (
        sa.Index(
            "ix_task_run_flow_run_id_task_key_dynamic_key",
            flow_run_id,
            task_key,
            dynamic_key,
            unique=True,
        ),
    )


class Deployment(Base):
    name = Column(String, nullable=False)
    flow_id = Column(UUID, ForeignKey("flow.id"), nullable=False, index=True)
    schedules = Column(Pydantic(List[schedules.Schedule]))

    flow = relationship(Flow, back_populates="deployments", lazy="raise")
