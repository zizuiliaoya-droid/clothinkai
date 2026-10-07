// 网络诊断响应类型，与后端 IpDiagnosticsResponse（backend/app/modules/security/schemas.py）一一对应。

export interface ForwardedHeader {
  name: string;
  /** 原始值；同名头出现多行时按出现顺序用 ", " 拼接；请求里没有为 null */
  value: string | null;
}

export interface XffHop {
  /** X-Forwarded-For 的一段（去首尾空白后的原文） */
  raw: string;
  /** 归一后的 IP（IPv4-mapped 还原成 IPv4）；解析不了为 null */
  ip: string | null;
  is_public: boolean;
}

export interface IpDiagnostics {
  /** uvicorn 处理转发头之后的 request.client.host */
  client_host: string | null;
  client_host_is_public: boolean;
  headers: ForwardedHeader[];
  xff_chain: XffHop[];
  forwarded_allow_ips_set: boolean;
  forwarded_allow_ips: string | null;
  uvicorn_version: string | null;
  /** ISO 8601（UTC） */
  server_time: string;
}
