"""安全模块 Pydantic Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ForwardedHeader(BaseModel):
    name: str = Field(..., description="请求头名称")
    value: str | None = Field(
        None,
        description="原始值，原样回显；同名头出现多行时按出现顺序用 ', ' 拼接；请求里没有则为 null",
    )


class XffHop(BaseModel):
    raw: str = Field(..., description="X-Forwarded-For 的一段（去首尾空白后的原文）")
    ip: str | None = Field(
        None, description="归一后的 IP（IPv4-mapped 还原成 IPv4）；解析不了为 null"
    )
    is_public: bool = Field(..., description="是否公网地址；私网 / 100.64/10 / 保留段等为 false")


class IpDiagnosticsResponse(BaseModel):
    client_host: str | None = Field(
        None, description="uvicorn 处理转发头之后的 request.client.host（系统认定的客户端 IP）"
    )
    client_host_is_public: bool = Field(..., description="client_host 是否公网地址")
    headers: list[ForwardedHeader] = Field(..., description="转发相关请求头的原始值（固定顺序）")
    xff_chain: list[XffHop] = Field(..., description="X-Forwarded-For 按逗号拆开后的每一段")
    forwarded_allow_ips_set: bool = Field(
        ..., description="进程环境变量 FORWARDED_ALLOW_IPS 是否设置（启动命令参数看不到）"
    )
    forwarded_allow_ips: str | None = Field(None, description="FORWARDED_ALLOW_IPS 的原值")
    uvicorn_version: str | None = Field(None, description="已安装的 uvicorn 版本")
    server_time: datetime = Field(..., description="服务器当前时间（UTC）")


__all__ = ["ForwardedHeader", "IpDiagnosticsResponse", "XffHop"]
