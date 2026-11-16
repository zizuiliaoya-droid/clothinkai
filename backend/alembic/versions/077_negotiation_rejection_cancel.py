"""谈款驳回原单重提与超时取消；只扩状态约束，不改现有业务数据。"""

import sqlalchemy as sa

from alembic import op

revision = "077_negotiation_rejection_cancel"
down_revision = "076_negotiation_creation_keys"
branch_labels = None
depends_on = None

_OLD = "status IN ('草稿','主管待审','审核驳回','待老板审核','老板退回','待补收货地址','已定稿')"
_NEW = "status IN ('草稿','主管待审','审核驳回','待老板审核','老板退回','待补收货地址','已定稿','已取消')"


def upgrade() -> None:
    op.drop_constraint("ck_negotiation_status", "negotiation", type_="check")
    op.create_check_constraint("ck_negotiation_status", "negotiation", _NEW)


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM negotiation WHERE status='已取消')")
    ):
        raise RuntimeError("已有自动取消谈款，不能回退到不认识该状态的版本")
    op.drop_constraint("ck_negotiation_status", "negotiation", type_="check")
    op.create_check_constraint("ck_negotiation_status", "negotiation", _OLD)
