"""
Demo models for the fasthx-admin example application.
"""

import enum

from sqlalchemy import Column, Integer, String, ForeignKey, Boolean, Enum as SAEnum
from sqlalchemy.orm import relationship

from fasthx_admin import Base


class BuildStatus(str, enum.Enum):
    IDLE = "idle"
    BUILDING = "building"
    SUCCESS = "success"
    FAILED = "failed"


class EdgeStatus(str, enum.Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    DEPLOYING = "deploying"
    ERROR = "error"
    PENDING = "pending"


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    sid = Column(String(50), unique=True, nullable=False)
    adom = Column(String(100), nullable=False)

    orchestrators = relationship("Orchestrator", back_populates="customer")
    edges = relationship("FortiEdge", back_populates="customer")

    # Metadata for the admin UI
    __admin_category__ = "Fortinet"
    __admin_icon__ = "building"
    __admin_name__ = "Customers"

    def __str__(self):
        return f"{self.name} ({self.sid})"

    def __repr__(self):
        return f"<Customer {self.name}>"


class Orchestrator(Base):
    __tablename__ = "orchestrators"

    id = Column(Integer, primary_key=True, index=True)
    address = Column(String(255), nullable=False)
    type = Column(String(50), nullable=False, default="FortiManager")
    apiname = Column(String(100), nullable=False)
    version = Column(String(20))
    build_status = Column(
        SAEnum(BuildStatus), default=BuildStatus.IDLE, nullable=False
    )
    dedicated_fortimanager = Column(Boolean, default=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)

    customer = relationship("Customer", back_populates="orchestrators")
    edges = relationship("FortiEdge", back_populates="orchestrator")

    __admin_category__ = "Fortinet"
    __admin_icon__ = "server"
    __admin_name__ = "Orchestrators"

    def __repr__(self):
        return f"<Orchestrator {self.address}>"


class FortiEdge(Base):
    __tablename__ = "edges"

    id = Column(Integer, primary_key=True, index=True)
    hostname = Column(String(100), nullable=False)
    serial_number = Column(String(50), unique=True, nullable=False)
    status = Column(SAEnum(EdgeStatus), default=EdgeStatus.PENDING, nullable=False)
    deploy_progress = Column(Integer, default=0)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    orchestrator_id = Column(Integer, ForeignKey("orchestrators.id"), nullable=True)

    customer = relationship("Customer", back_populates="edges")
    orchestrator = relationship("Orchestrator", back_populates="edges")

    __admin_category__ = "Fortinet"
    __admin_icon__ = "shield"
    __admin_name__ = "FortiEdges"

    def __repr__(self):
        return f"<FortiEdge {self.hostname}>"
