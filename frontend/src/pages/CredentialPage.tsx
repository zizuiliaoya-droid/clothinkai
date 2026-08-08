import { useState } from "react";
import {
  Button,
  Card,
  Checkbox,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { EditOutlined, PlusOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import {
  createCredential,
  deleteCredential,
  listCredentials,
  pauseCredential,
  resumeCredential,
  updateCredential,
} from "@/features/credential/api";
import {
  CREDENTIAL_PLATFORMS,
  type Credential,
  type CredentialCreatePayload,
  type CredentialPlatform,
  type CredentialStatus,
  type CredentialUpdatePayload,
} from "@/features/credential/types";
import { extractErrorMessage } from "@/services/apiClient";
import { useAuthStore } from "@/stores/authStore";

const STATUS_META: Record<CredentialStatus, { color: string; label: string }> = {
  active: { color: "success", label: "启用" },
  paused: { color: "warning", label: "已暂停" },
  disabled: { color: "error", label: "已禁用" },
};

export function CredentialPage() {
  const queryClient = useQueryClient();
  const canManage = useAuthStore(
    (state) =>
      state.user?.roles.some(
        (role) => role === "admin" || role === "platform_admin",
      ) ?? false,
  );
  const [createForm] = Form.useForm<CredentialCreatePayload>();
  const [editForm] = Form.useForm<CredentialUpdatePayload>();
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<Credential | null>(null);
  const [platform, setPlatform] = useState<CredentialPlatform>();
  const [status, setStatus] = useState<CredentialStatus>();
  const [page, setPage] = useState(1);
  const pageSize = 20;

  const queryKey = ["credentials", platform, status, page, pageSize] as const;
  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () =>
      listCredentials({
        platform,
        cred_status: status,
        page,
        page_size: pageSize,
      }),
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["credentials"] });
  const createMutation = useMutation({
    mutationFn: createCredential,
    onSuccess: async () => {
      message.success("平台凭据创建成功，默认处于暂停状态");
      setCreateOpen(false);
      createForm.resetFields();
      await refresh();
    },
    onError: (error) => message.error(extractErrorMessage(error, "创建失败")),
  });
  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: CredentialUpdatePayload }) =>
      updateCredential(id, payload),
    onSuccess: async () => {
      message.success("凭据信息已更新");
      setEditing(null);
      editForm.resetFields();
      await refresh();
    },
    onError: (error) => message.error(extractErrorMessage(error, "更新失败")),
  });

  const statusMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "pause" | "resume" }) =>
      action === "pause" ? pauseCredential(id) : resumeCredential(id),
    onSuccess: async (_result, variables) => {
      message.success(variables.action === "pause" ? "凭据已暂停" : "凭据已恢复");
      await refresh();
    },
    onError: (error) => message.error(extractErrorMessage(error, "操作失败")),
  });
  const deleteMutation = useMutation({
    mutationFn: deleteCredential,
    onSuccess: async () => {
      message.success("凭据已删除");
      await refresh();
    },
    onError: (error) => message.error(extractErrorMessage(error, "删除失败")),
  });

  function openEdit(record: Credential) {
    setEditing(record);
    editForm.setFieldsValue({ password: undefined, remark: record.remark });
  }

  function confirmDelete(record: Credential) {
    Modal.confirm({
      title: "确认删除平台凭据？",
      content: `${record.platform} / ${record.username} 将被永久删除，已保存的密文也会清除。`,
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: () => deleteMutation.mutateAsync(record.id),
    });
  }

  const columns: ColumnsType<Credential> = [
    { title: "平台", dataIndex: "platform", width: 110, fixed: "left" },
    { title: "账号", dataIndex: "username", width: 180, fixed: "left" },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (value: CredentialStatus) => (
        <Tag color={STATUS_META[value].color}>{STATUS_META[value].label}</Tag>
      ),
    },

    { title: "失败次数", dataIndex: "consecutive_failures", width: 100 },
    {
      title: "最后失败原因",
      dataIndex: "last_failure_reason",
      width: 220,
      ellipsis: true,
      render: (value: string | null) => value ?? "—",
    },
    { title: "备注", dataIndex: "remark", width: 200, ellipsis: true, render: (v) => v ?? "—" },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      width: 180,
      render: (value: string) => new Date(value).toLocaleString(),
    },
    {
      title: "操作",
      key: "actions",
      width: 230,
      fixed: "right",
      render: (_value, record) => (
        <Space size={0}>
          <Button type="link" icon={<EditOutlined />} onClick={() => openEdit(record)}>
            改密码/备注
          </Button>
          {record.status === "active" ? (
            <Button type="link" onClick={() => statusMutation.mutate({ id: record.id, action: "pause" })}>
              暂停
            </Button>
          ) : (
            <Button type="link" onClick={() => statusMutation.mutate({ id: record.id, action: "resume" })}>
              恢复
            </Button>
          )}
          <Button danger type="link" onClick={() => confirmDelete(record)}>
            删除
          </Button>
        </Space>
      ),
    },
  ];
  const visibleColumns = canManage
    ? columns
    : columns.filter((column) => column.key !== "actions");

  return (
    <Card
      title={<Typography.Title level={4} style={{ margin: 0 }}>平台凭据</Typography.Title>}
      extra={
        canManage ? (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            创建凭据
          </Button>
        ) : undefined
      }
    >

      <Space wrap style={{ marginBottom: 16 }}>
        <span>平台：</span>
        <Select<CredentialPlatform>
          aria-label="平台筛选"
          allowClear
          placeholder="全部平台"
          style={{ width: 140 }}
          options={CREDENTIAL_PLATFORMS.map((value) => ({ label: value, value }))}
          value={platform}
          onChange={(value) => { setPlatform(value); setPage(1); }}
        />
        <span>状态：</span>
        <Select<CredentialStatus>
          aria-label="状态筛选"
          allowClear
          placeholder="全部状态"
          style={{ width: 140 }}
          options={Object.entries(STATUS_META).map(([value, meta]) => ({
            label: meta.label,
            value: value as CredentialStatus,
          }))}
          value={status}
          onChange={(value) => { setStatus(value); setPage(1); }}
        />
      </Space>
      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={visibleColumns}
        dataSource={data?.items ?? []}
        scroll={{ x: 1450 }}
        pagination={{
          current: page,
          pageSize,
          total: data?.total ?? 0,
          showSizeChanger: false,
          showTotal: (total) => `共 ${total} 条`,
          onChange: setPage,
        }}
      />

      <Modal
        title="创建平台凭据"
        open={createOpen}
        okText="创建"
        cancelText="取消"
        confirmLoading={createMutation.isPending}
        onOk={() => createForm.submit()}
        onCancel={() => { setCreateOpen(false); createForm.resetFields(); }}
        destroyOnHidden
      >
        <Form form={createForm} layout="vertical" onFinish={(values) => createMutation.mutate(values)}>

          <Form.Item name="platform" label="平台" rules={[{ required: true, message: "请选择平台" }]}>
            <Select options={CREDENTIAL_PLATFORMS.map((value) => ({ label: value, value }))} />
          </Form.Item>
          <Form.Item
            name="username"
            label="平台账号"
            rules={[{ required: true, message: "请输入平台账号" }, { max: 128 }]}
          >
            <Input autoComplete="username" maxLength={128} />
          </Form.Item>
          <Form.Item
            name="password"
            label="平台密码"
            rules={[{ required: true, message: "请输入平台密码" }]}
            extra="密码仅用于加密保存，创建后不会回显。"
          >
            <Input.Password autoComplete="new-password" visibilityToggle />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={3} maxLength={500} showCount />
          </Form.Item>
          <Form.Item
            name="privacy_consent"
            valuePropName="checked"
            rules={[{
              validator: (_rule, checked) =>
                checked ? Promise.resolve() : Promise.reject(new Error("请确认已获得授权并同意安全存储")),
            }]}
          >
            <Checkbox>我确认已获得该平台账号的使用授权，并同意系统加密存储凭据</Checkbox>
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={editing ? `修改凭据 · ${editing.platform} / ${editing.username}` : "修改凭据"}
        open={!!editing}
        okText="保存"
        cancelText="取消"
        confirmLoading={updateMutation.isPending}
        onOk={() => editForm.submit()}
        onCancel={() => { setEditing(null); editForm.resetFields(); }}
        destroyOnHidden
      >

        <Form
          form={editForm}
          layout="vertical"
          onFinish={(values) => {
            if (!editing) return;
            const payload = { ...values };
            if (!payload.password) delete payload.password;
            updateMutation.mutate({ id: editing.id, payload });
          }}
        >
          <Form.Item
            name="password"
            label="新密码"
            extra="留空表示不修改；现有密码不会回显。"
          >
            <Input.Password autoComplete="new-password" visibilityToggle />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={3} maxLength={500} showCount />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
