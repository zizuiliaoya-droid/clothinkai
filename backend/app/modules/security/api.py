"""安全模块 API（/api/security）。

``GET /api/security/ip-diagnostics``：管理员专用的网络诊断，确认后端拿到的是不是用户真实出口 IP、
转发链长什么样、该信任哪几跳（判读表见 ``docs/requirements/20261005-对比/report-D-ip-whitelist.md`` §2.3）。

- 只读：不碰库、不写审计；响应带 ``Cache-Control: no-store``，浏览器 / 中间代理不缓存
- 原始转发头会暴露内部网络结构，只给持 ``security.ip_allowlist:write`` 的人看（眼下只有 admin /
  platform_admin，见 ``permissions.py``）；前端按角色隐藏 Tab 只是展示，闸门在这里
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.modules.auth.deps import require_permission
from app.modules.security.permissions import SCOPE_IP_ALLOWLIST
from app.modules.security.schemas import IpDiagnosticsResponse
from app.modules.security.service import build_ip_diagnostics

router = APIRouter(prefix="/api/security", tags=["security"])


@router.get(
    "/ip-diagnostics",
    response_model=IpDiagnosticsResponse,
    dependencies=[require_permission(SCOPE_IP_ALLOWLIST, "write")],
)
async def get_ip_diagnostics(request: Request, response: Response) -> IpDiagnosticsResponse:
    """返回系统看到的客户端 IP、转发相关请求头原值与 XFF 拆分结果。"""
    response.headers["Cache-Control"] = "no-store"
    return build_ip_diagnostics(request)


__all__ = ["router"]
