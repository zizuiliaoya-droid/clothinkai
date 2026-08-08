import { useMemo, useState } from "react";
import {
  Button,
  Card,
  Col,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { CheckCircleOutlined, ExclamationCircleOutlined, InfoCircleOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { listDataQualityIssues, getDataQualitySummary, resolveDataQualityIssue } from "@/features/collect/api";
import type {
  DataQualityIssue,
  DataQualityResolution,
  DataQualitySeverity,
  DataQualityStatus,
} from "@/features/collect/types";
import { extractErrorMessage } from "@/services/apiClient";
import { useAuthStore } from "@/stores/authStore";

const SEVERITY_META: Record<DataQualitySeverity, { color: string; label: string }> = {
  error: { color: "error", label: "错误" },
  warning: { color: "warning", label: "警告" },
  info: { color: "processing", label: "提示" },
};
const STATUS_META: Record<DataQualityStatus, { color: string; label: string }> = {
  open: { color: "error", label: "待处理" },
  fixed: { color: "success", label: "已修复" },
  ignored: { color: "default", label: "已忽略" },
};

export function DataQualityPage() {
  const queryClient = useQueryClient();
  const canResolve = useAuthStore(
    (state) =>
      state.user?.roles.some(
        (role) => role === "admin" || role === "platform_admin",
      ) ?? false,
  );
  const [source, setSource] = useState<string>();
  const [severity, setSeverity] = useState<DataQualitySeverity>();
  const [status, setStatus] = useState<DataQualityStatus>();
  const [page, setPage] = useState(1);
  const pageSize = 20;

  const summaryQuery = useQuery({
    queryKey: ["data-quality-summary"],
    queryFn: getDataQualitySummary,
  });
  const issuesQuery = useQuery({
    queryKey: ["data-quality-issues", source, severity, status, page, pageSize],
    queryFn: () =>
      listDataQualityIssues({
        source,
        severity,
        issue_status: status,
        page,
        page_size: pageSize,
      }),
  });
  const resolveMutation = useMutation({
    mutationFn: ({ id, resolution }: { id: string; resolution: DataQualityResolution }) =>
      resolveDataQualityIssue(id, resolution),
    onSuccess: async () => {
      message.success("数据质量问题状态已更新");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["data-quality-issues"] }),
        queryClient.invalidateQueries({ queryKey: ["data-quality-summary"] }),
      ]);
    },
    onError: (error) => message.error(extractErrorMessage(error, "处理失败")),
  });

  const summary = useMemo(() => {
    const totals: Record<DataQualitySeverity, number> = { error: 0, warning: 0, info: 0 };
    for (const row of summaryQuery.data ?? []) totals[row.severity] += row.count;
    return totals;
  }, [summaryQuery.data]);
  const sourceOptions = useMemo(
    () =>
      Array.from(new Set((summaryQuery.data ?? []).map((row) => row.source))).map(
        (value) => ({ label: value, value }),
      ),
    [summaryQuery.data],
  );

  const columnsWithActions: ColumnsType<DataQualityIssue> = [
    { title: "来源", dataIndex: "source", width: 130, fixed: "left" },

    {
      title: "严重度",
      dataIndex: "severity",
      width: 100,
      render: (value: DataQualitySeverity) => (
        <Tag color={SEVERITY_META[value].color}>{SEVERITY_META[value].label}</Tag>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (value: DataQualityStatus) => (
        <Tag color={STATUS_META[value].color}>{STATUS_META[value].label}</Tag>
      ),
    },
    { title: "实体类型", dataIndex: "entity_type", width: 130, render: (v) => v ?? "—" },
    { title: "实体引用", dataIndex: "entity_ref", width: 160, render: (v) => v ?? "—" },
    { title: "问题描述", dataIndex: "message", width: 360 },
    {
      title: "发现时间",
      dataIndex: "created_at",
      width: 180,
      render: (value: string) => new Date(value).toLocaleString(),
    },
    {
      title: "操作",
      key: "actions",
      width: 150,
      fixed: "right",
      render: (_value, record) =>
        record.status === "open" ? (
          <Space size={0}>
            <Button
              type="link"
              loading={resolveMutation.isPending && resolveMutation.variables?.id === record.id}
              onClick={() => resolveMutation.mutate({ id: record.id, resolution: "fixed" })}
            >
              标记已修复
            </Button>
            <Button
              type="link"
              danger
              loading={resolveMutation.isPending && resolveMutation.variables?.id === record.id}
              onClick={() => resolveMutation.mutate({ id: record.id, resolution: "ignored" })}
            >
              忽略
            </Button>
          </Space>
        ) : (
          "—"
        ),
    },
  ];
  const columns = canResolve
    ? columnsWithActions
    : columnsWithActions.filter((column) => column.key !== "actions");

  return (
    <div>
      <Typography.Title level={4}>数据质量</Typography.Title>
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={8}>
          <Card loading={summaryQuery.isLoading}>
            <Statistic
              title="错误"
              value={summary.error}
              valueStyle={{ color: "#cf1322" }}
              prefix={<ExclamationCircleOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card loading={summaryQuery.isLoading}>
            <Statistic
              title="警告"
              value={summary.warning}
              valueStyle={{ color: "#d48806" }}
              prefix={<InfoCircleOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card loading={summaryQuery.isLoading}>
            <Statistic
              title="提示"
              value={summary.info}
              valueStyle={{ color: "#1677ff" }}
              prefix={<CheckCircleOutlined />}
            />
          </Card>
        </Col>
      </Row>

      <Card>
        <Space wrap style={{ marginBottom: 16 }}>
          <span>来源：</span>
          <Select
            aria-label="数据来源"
            allowClear
            placeholder="全部来源"
            style={{ width: 150 }}
            value={source}
            options={sourceOptions}
            onChange={(value) => { setSource(value); setPage(1); }}
          />
          <span>严重度：</span>
          <Select<DataQualitySeverity>
            aria-label="严重度"
            allowClear
            placeholder="全部严重度"

            style={{ width: 140 }}
            value={severity}
            options={Object.entries(SEVERITY_META).map(([value, meta]) => ({
              label: meta.label,
              value: value as DataQualitySeverity,
            }))}
            onChange={(value) => { setSeverity(value); setPage(1); }}
          />
          <span>状态：</span>
          <Select<DataQualityStatus>
            aria-label="处理状态"
            allowClear
            placeholder="全部状态"
            style={{ width: 140 }}
            value={status}
            options={Object.entries(STATUS_META).map(([value, meta]) => ({
              label: meta.label,
              value: value as DataQualityStatus,
            }))}
            onChange={(value) => { setStatus(value); setPage(1); }}
          />
        </Space>
        <Table
          rowKey="id"
          size="small"
          loading={issuesQuery.isLoading}
          columns={columns}
          dataSource={issuesQuery.data?.items ?? []}
          scroll={{ x: 1250 }}
          pagination={{
            current: page,
            pageSize,
            total: issuesQuery.data?.total ?? 0,
            showSizeChanger: false,
            showTotal: (total) => `共 ${total} 条`,
            onChange: setPage,
          }}
        />
      </Card>
    </div>
  );
}
