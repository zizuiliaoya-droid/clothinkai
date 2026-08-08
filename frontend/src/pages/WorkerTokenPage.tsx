import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { CopyOutlined, KeyOutlined, PlusOutlined, StopOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import {
  issueWorkerToken,
  listWorkerTokens,
  revokeWorkerToken,
} from "@/features/collect/api";
import type {
  WorkerToken,
  WorkerTokenCreatePayload,
  WorkerTokenIssued,
} from "@/features/collect/types";
import { extractErrorMessage } from "@/services/apiClient";

interface IssueFormValues {
  name: string;
  ip_allowlist: string;
}

export function WorkerTokenPage() {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<IssueFormValues>();
  const [createOpen, setCreateOpen] = useState(false);
  const [issued, setIssued] = useState<WorkerTokenIssued | null>(null);
  const tokensQuery = useQuery({
    queryKey: ["worker-tokens"],
    queryFn: listWorkerTokens,
  });

  const issueMutation = useMutation({
    mutationFn: (payload: WorkerTokenCreatePayload) => issueWorkerToken(payload),
    onSuccess: async (result) => {
      setCreateOpen(false);
      form.resetFields();
      setIssued(result);
      message.success("Worker Token 签发成功");
      await queryClient.invalidateQueries({ queryKey: ["worker-tokens"] });
    },
    onError: (error) => message.error(extractErrorMessage(error, "签发失败")),
  });

  const revokeMutation = useMutation({
    mutationFn: revokeWorkerToken,
    onSuccess: async () => {
      message.success("Worker Token 已吊销");
      await queryClient.invalidateQueries({ queryKey: ["worker-tokens"] });
    },
    onError: (error) => message.error(extractErrorMessage(error, "吊销失败")),
  });

  function handleIssue(values: IssueFormValues) {
    const ipAllowlist = (values.ip_allowlist ?? "")
      .split(/[\n,]/)
      .map((value) => value.trim())
      .filter(Boolean);
    issueMutation.mutate({ name: values.name, ip_allowlist: ipAllowlist });
  }

  async function handleCopy() {
    if (!issued) return;
    try {
      await navigator.clipboard.writeText(issued.token);
      message.success("Token 已复制到剪贴板");
    } catch {
      message.error("复制失败，请手动复制");
    }
  }

  function closeIssuedModal() {
    setIssued(null);
  }

  const columns: ColumnsType<WorkerToken> = [
    { title: "名称", dataIndex: "name", width: 180, fixed: "left" },
    {
      title: "IP 白名单",
      dataIndex: "ip_allowlist",
      width: 260,
      render: (values: string[]) =>
        values.length ? values.map((value) => <Tag key={value}>{value}</Tag>) : "—",
    },

    {
      title: "状态",
      dataIndex: "is_active",
      width: 100,
      render: (active: boolean) => (
        <Tag color={active ? "success" : "default"}>{active ? "有效" : "已吊销"}</Tag>
      ),
    },
    { title: "连续鉴权失败", dataIndex: "consecutive_auth_failures", width: 130 },
    {
      title: "最后访问",
      dataIndex: "last_seen_at",
      width: 180,
      render: (value: string | null) => (value ? new Date(value).toLocaleString() : "—"),
    },
    {
      title: "签发时间",
      dataIndex: "created_at",
      width: 180,
      render: (value: string) => new Date(value).toLocaleString(),
    },
    {
      title: "操作",
      key: "actions",
      width: 100,
      fixed: "right",
      render: (_value, record) => (
        <Button
          danger
          type="link"
          icon={<StopOutlined />}
          disabled={!record.is_active}
          loading={revokeMutation.isPending && revokeMutation.variables === record.id}
          onClick={() =>
            Modal.confirm({
              title: "确认吊销 Worker Token？",
              content: `吊销后 ${record.name} 将无法继续鉴权，且不可恢复。`,
              okText: "吊销",
              okButtonProps: { danger: true },
              cancelText: "取消",
              onOk: () => revokeMutation.mutateAsync(record.id),
            })
          }
        >
          吊销
        </Button>
      ),
    },
  ];

  return (
    <Card
      title={
        <Space>
          <KeyOutlined />
          <Typography.Title level={4} style={{ margin: 0 }}>
            Worker Token
          </Typography.Title>
        </Space>
      }
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          签发 Token
        </Button>
      }
    >
      <Table
        rowKey="id"
        size="small"
        loading={tokensQuery.isLoading}
        columns={columns}
        dataSource={tokensQuery.data ?? []}
        scroll={{ x: 1050 }}
        pagination={false}
      />

      <Modal
        title="签发 Worker Token"
        open={createOpen}
        okText="签发"
        cancelText="取消"
        confirmLoading={issueMutation.isPending}
        onOk={() => form.submit()}
        onCancel={() => {
          setCreateOpen(false);
          form.resetFields();
        }}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={handleIssue}>
          <Form.Item
            name="name"
            label="Token 名称"
            rules={[{ required: true, message: "请输入 Token 名称" }, { max: 64 }]}
          >
            <Input placeholder="例如：生产采集 Worker 01" maxLength={64} />
          </Form.Item>

          <Form.Item
            name="ip_allowlist"
            label="IP 白名单"
            rules={[{ required: true, message: "请至少输入一个 IP 或 CIDR" }]}
            extra="每行或用逗号分隔一个 IP/CIDR；支持 IPv4 和 IPv6。"
          >
            <Input.TextArea rows={4} placeholder={"10.0.0.10\n10.0.1.0/24"} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="Worker Token 已签发"
        open={!!issued}
        okText="我已安全保存"
        cancelButtonProps={{ style: { display: "none" } }}
        closable={false}
        maskClosable={false}
        keyboard={false}
        onOk={closeIssuedModal}
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="明文 Token 仅显示一次"
          description="请立即复制并保存在安全的密钥管理工具中。关闭此窗口后无法再次查看。"
        />
        <Form layout="vertical">
          <Form.Item label="Token 名称">
            <Input value={issued?.name} readOnly />
          </Form.Item>
          <Form.Item label="明文 Token">
            <Input.TextArea
              value={issued?.token}
              readOnly
              autoSize={{ minRows: 3, maxRows: 6 }}
            />
          </Form.Item>
        </Form>
        <Button type="primary" icon={<CopyOutlined />} onClick={() => void handleCopy()}>
          复制 Token
        </Button>
      </Modal>
    </Card>
  );
}
