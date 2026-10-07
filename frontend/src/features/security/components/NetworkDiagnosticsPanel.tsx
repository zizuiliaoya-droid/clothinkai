// 系统设置 →「网络诊断」Tab（只对 admin / platform_admin 显示；真正的闸门是后端
// security.ip_allowlist:write 的 403）。
//
// 用途：业务在办公室网络、手机 4G 下各打开一次，把「系统看到的 IP + 转发链」复制给开发，
// 用来判断后端能不能拿到真实出口 IP、该信任哪几跳代理。只读，不改任何配置。

import { useMemo, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import {
  Alert,
  Button,
  Collapse,
  Descriptions,
  Input,
  Skeleton,
  Space,
  Table,
  Tag,
  Typography,
  message,
  theme,
} from "antd";
import type { DescriptionsProps, TableColumnsType } from "antd";
import {
  CheckCircleOutlined,
  CopyOutlined,
  ExclamationCircleOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { getIpDiagnostics } from "@/features/security/api";
import type { ForwardedHeader, IpDiagnostics, XffHop } from "@/features/security/types";
import { extractErrorMessage } from "@/services/apiClient";

const RAW_PANEL_KEY = "raw";
// 长 IPv6 与请求头原值在 375px 下必须能折行，不能把页面撑出横向滚动
const BREAK_ALL: CSSProperties = { wordBreak: "break-all" };
// 手机上的两个主要操作：触控目标不小于 44px
const TOUCH_TARGET: CSSProperties = { minHeight: 44 };

interface HopRow extends XffHop {
  index: number;
}

type ReadableColors = Record<"muted" | "body" | "successIcon" | "warningIcon", CSSProperties>;

// 文字在浅底上的对比度要 ≥ 4.5:1（WCAG AA）：
// - 次要文字用 colorTextLabel（约 6.9:1）；antd 的 secondary 灰、Descriptions 标签灰只有约 3.3:1
// - 状态 Tag 的底色、边框、图标沿用 antd 状态色，文字改用正文色。默认主题里
//   colorSuccessText / colorWarningText 就等于状态色本身（#52c41a / #faad14），当文字用在
//   浅底上只有 2.2:1 / 1.8:1，「非公网地址」这个最关键的提示会看不清
function useReadableColors(): ReadableColors {
  const { token } = theme.useToken();
  return {
    muted: { color: token.colorTextLabel },
    body: { color: token.colorText },
    successIcon: { color: token.colorSuccess },
    warningIcon: { color: token.colorWarning },
  };
}

function publicText(isPublic: boolean): string {
  return isPublic ? "公网" : "非公网";
}

function hopText(hop: XffHop): string {
  return hop.ip === null
    ? `${hop.raw}（无法解析）`
    : `${hop.ip}（${publicText(hop.is_public)}）`;
}

function allowIpsText(data: IpDiagnostics): string {
  if (!data.forwarded_allow_ips_set) return "未设置";
  return data.forwarded_allow_ips || "（空字符串）";
}

// 「复制全部」的内容：前半段给人看，后半段 JSON 原文给开发对照
function buildReportText(data: IpDiagnostics, fetchedAt: Date): string {
  const hops = data.xff_chain.length
    ? data.xff_chain.map((hop, i) => `${i + 1}. ${hopText(hop)}`).join("；")
    : "（无）";
  return [
    "【网络诊断结果】",
    `获取时间（本机）：${fetchedAt.toLocaleString("zh-CN")}`,
    `系统看到的 IP：${data.client_host ?? "（未知）"}（${publicText(data.client_host_is_public)}）`,
    `X-Forwarded-For 拆分：${hops}`,
    ...data.headers.map((h) => `${h.name}：${h.value ?? "（无）"}`),
    `FORWARDED_ALLOW_IPS：${allowIpsText(data)}`,
    `uvicorn：${data.uvicorn_version ?? "未知"}`,
    `服务器时间：${data.server_time}`,
    "---- JSON ----",
    JSON.stringify(data, null, 2),
  ].join("\n");
}

function ClientHostSummary({ data }: { data: IpDiagnostics }) {
  const colors = useReadableColors();
  // 状态用文字 + 图标表达，不只靠颜色
  let tag: ReactNode;
  if (data.client_host === null) {
    tag = <Tag>无法识别</Tag>;
  } else if (data.client_host_is_public) {
    tag = (
      <Tag
        color="success"
        style={colors.body}
        icon={<CheckCircleOutlined style={colors.successIcon} />}
      >
        公网地址
      </Tag>
    );
  } else {
    tag = (
      <Tag
        color="warning"
        style={colors.body}
        icon={<ExclamationCircleOutlined style={colors.warningIcon} />}
      >
        非公网地址（内网 / 保留段）
      </Tag>
    );
  }
  return (
    <div style={{ marginBottom: 16 }}>
      <Typography.Text style={colors.muted}>系统看到的你的 IP</Typography.Text>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: 12,
          marginTop: 4,
        }}
      >
        <Typography.Title
          level={3}
          style={{ margin: 0, minWidth: 0, maxWidth: "100%", ...BREAK_ALL }}
        >
          {data.client_host ?? "（未知）"}
        </Typography.Title>
        {tag}
      </div>
      <Typography.Paragraph style={{ marginTop: 8, marginBottom: 0 }}>
        {data.client_host_is_public
          ? "可以打开 ip.sb 对照，两边一致说明识别正确。"
          : "这通常是服务器入口的内网地址，系统暂时拿不到你的真实出口 IP，请把结果发给开发。"}
      </Typography.Paragraph>
    </div>
  );
}

function DiagnosticsDetails({ data }: { data: IpDiagnostics }) {
  const colors = useReadableColors();
  const headerColumns: TableColumnsType<ForwardedHeader> = [
    {
      title: "请求头",
      dataIndex: "name",
      width: 136,
      render: (name: string) => <span style={BREAK_ALL}>{name}</span>,
    },
    {
      title: "原始值",
      dataIndex: "value",
      render: (value: string | null) =>
        value === null ? (
          <span style={colors.muted}>（无）</span>
        ) : (
          <span style={BREAK_ALL}>{value}</span>
        ),
    },
  ];
  const hopColumns: TableColumnsType<HopRow> = [
    { title: "#", dataIndex: "index", width: 44 },
    {
      title: "地址",
      key: "address",
      render: (_: unknown, hop: HopRow) => (
        <span style={BREAK_ALL}>{hop.ip ?? `${hop.raw}（无法解析）`}</span>
      ),
    },
    {
      title: "公网",
      dataIndex: "is_public",
      width: 88,
      render: (isPublic: boolean) => (
        <Tag color={isPublic ? "success" : "default"} style={colors.body}>
          {isPublic ? "是" : "否"}
        </Tag>
      ),
    },
  ];
  const hopRows: HopRow[] = data.xff_chain.map((hop, i) => ({ ...hop, index: i + 1 }));
  const label = (text: string) => <span style={colors.muted}>{text}</span>;
  const infoItems: DescriptionsProps["items"] = [
    {
      key: "server_time",
      label: label("服务器时间"),
      children: (
        <span style={BREAK_ALL}>
          {`${new Date(data.server_time).toLocaleString("zh-CN")}（${data.server_time}）`}
        </span>
      ),
    },
    { key: "uvicorn", label: label("uvicorn 版本"), children: data.uvicorn_version ?? "未知" },
    {
      key: "forwarded_allow_ips",
      label: label("FORWARDED_ALLOW_IPS"),
      children: data.forwarded_allow_ips_set ? (
        <span style={BREAK_ALL}>{allowIpsText(data)}</span>
      ) : (
        "未设置（uvicorn 默认只信任 127.0.0.1）"
      ),
    },
  ];

  return (
    <>
      <Typography.Title level={5}>转发相关请求头</Typography.Title>
      <Table<ForwardedHeader>
        rowKey="name"
        size="small"
        pagination={false}
        tableLayout="fixed"
        columns={headerColumns}
        dataSource={data.headers}
        style={{ marginBottom: 24 }}
      />
      <Typography.Title level={5}>X-Forwarded-For 拆分</Typography.Title>
      <Table<HopRow>
        rowKey="index"
        size="small"
        pagination={false}
        tableLayout="fixed"
        columns={hopColumns}
        dataSource={hopRows}
        locale={{ emptyText: "请求里没有 X-Forwarded-For" }}
        style={{ marginBottom: 24 }}
      />
      <Descriptions
        size="small"
        bordered
        column={1}
        layout="vertical"
        items={infoItems}
        style={{ marginBottom: 24 }}
      />
    </>
  );
}

export function NetworkDiagnosticsPanel() {
  const [rawOpen, setRawOpen] = useState(false);
  const { data, error, isLoading, isFetching, refetch, dataUpdatedAt } = useQuery({
    queryKey: ["ip-diagnostics"],
    queryFn: getIpDiagnostics,
    // 换个网络结果就不同：每次打开 / 刷新都现取
    staleTime: 0,
    // 403 / 401 重试没有意义
    retry: false,
  });

  const reportText = useMemo(
    () => (data ? buildReportText(data, new Date(dataUpdatedAt)) : ""),
    [data, dataUpdatedAt]
  );

  async function handleCopy() {
    if (!reportText) return;
    try {
      await navigator.clipboard.writeText(reportText);
      message.success("已复制，请发给开发");
    } catch {
      // 非 HTTPS 页面、部分手机内置浏览器没有剪贴板权限：展开原文让用户长按手动复制
      setRawOpen(true);
      message.error("复制失败，请在下方原文里长按全选后手动复制");
    }
  }

  return (
    <div>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="请分别在办公室网络和手机 4G 网络下打开本页，点「复制全部」，把结果发给开发。"
        description="只读诊断：用来确认系统能不能拿到你的真实出口 IP，不会改动任何配置。"
      />
      {isLoading ? (
        <Skeleton active />
      ) : (
        <>
          {error ? (
            <Alert
              type="error"
              showIcon
              style={{ marginBottom: 16 }}
              message={extractErrorMessage(error, "诊断信息加载失败")}
            />
          ) : null}
          {data ? <ClientHostSummary data={data} /> : null}
          <Space wrap style={{ marginBottom: 24 }}>
            <Button
              size="large"
              style={TOUCH_TARGET}
              icon={<ReloadOutlined />}
              loading={isFetching}
              onClick={() => void refetch()}
            >
              刷新
            </Button>
            <Button
              size="large"
              style={TOUCH_TARGET}
              type="primary"
              icon={<CopyOutlined />}
              disabled={!data}
              onClick={() => void handleCopy()}
            >
              复制全部
            </Button>
          </Space>
          {data ? (
            <>
              <DiagnosticsDetails data={data} />
              <Collapse
                activeKey={rawOpen ? [RAW_PANEL_KEY] : []}
                onChange={(keys) =>
                  setRawOpen((Array.isArray(keys) ? keys : [keys]).includes(RAW_PANEL_KEY))
                }
                items={[
                  {
                    key: RAW_PANEL_KEY,
                    label: "查看原文（复制失败时用）",
                    children: (
                      <Input.TextArea
                        readOnly
                        value={reportText}
                        autoSize={{ minRows: 4, maxRows: 12 }}
                        aria-label="诊断结果原文"
                      />
                    ),
                  },
                ]}
              />
            </>
          ) : null}
        </>
      )}
    </div>
  );
}
