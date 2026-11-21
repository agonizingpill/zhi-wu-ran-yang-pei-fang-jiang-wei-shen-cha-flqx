from datetime import datetime
from sqlalchemy import String,ForeignKey,JSON,DateTime,CheckConstraint,UniqueConstraint,func
from sqlalchemy.orm import Mapped,mapped_column
from .database import Base
class IssueCorrection(Base):
    """A supervisor-confirmed exchange of material attribution between two batches.

    The original DyeIssue rows are never rewritten; this record stores both the
    original and the corrected attribution so the source credentials stay intact.
    """
    __tablename__='issue_corrections'
    __table_args__=(UniqueConstraint('batch_low_id','batch_high_id'),
                    CheckConstraint('batch_low_id < batch_high_id'))
    id:Mapped[int]=mapped_column(primary_key=True)
    batch_low_id:Mapped[int]=mapped_column(ForeignKey('dye_batches.id'))
    batch_high_id:Mapped[int]=mapped_column(ForeignKey('dye_batches.id'))
    state:Mapped[str]=mapped_column(String(24),default='applied')
    request_id:Mapped[str]=mapped_column(String(64),unique=True)
    evidence:Mapped[dict]=mapped_column(JSON)
    original_snapshot:Mapped[dict]=mapped_column(JSON)
    applied_snapshot:Mapped[dict]=mapped_column(JSON)
    confirmed_by:Mapped[str]=mapped_column(String(120),default='')
    created_at:Mapped[object]=mapped_column(DateTime,server_default=func.now())
