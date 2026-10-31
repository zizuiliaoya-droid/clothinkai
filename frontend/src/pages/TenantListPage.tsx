import { useState } from "react";
import { Alert, Button, Card, Form, Input, Modal, Popconfirm, Space, Table, Tag, Typography, message } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { createTenant, listTenants, setTenantStatus } from "@/features/tenant/api";
import type { TenantCreate, TenantCreated, TenantItem } from "@/features/tenant/api";
import { useAuthStore } from "@/stores/authStore";
import { extractErrorMessage } from "@/services/apiClient";

export function TenantListPage() {
  const user = useAuthStore((state) => state.user);
  if (!user?.roles.includes("platform_admin")) return <Alert type="warning" message="仅平台管理员可管理租户" />;
  // 退出/换账号时整个表单与一次性凭据状态随身份销毁。
  return <TenantManager key={user.id} viewerId={user.id} />;
}

function TenantManager({ viewerId }: { viewerId: string }) {
  const client = useQueryClient();
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState(false);
  const [credentials, setCredentials] = useState<TenantCreated | null>(null);
  const [form] = Form.useForm<TenantCreate>();
  const list = useQuery({ queryKey: ["tenants", viewerId, page], queryFn: () => listTenants(page), retry: false });
  const create = useMutation({
    mutationFn: createTenant,
    gcTime: 0,
    onSuccess: (result) => {
      setOpen(false);
      form.resetFields();
      setCredentials(result);
      void client.invalidateQueries({ queryKey: ["tenants", viewerId] });
    },
  });
  const changeStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: "active" | "disabled" }) => setTenantStatus(id, status),
    onSuccess: (_result, input) => {
      void message.success(input.status === "active" ? "租户已启用，请客户重新登录" : "租户已停用，原登录已失效");
      void client.invalidateQueries({ queryKey: ["tenants", viewerId] });
    },
    onError: (error) => void message.error(extractErrorMessage(error, "修改租户状态失败")),
  });
  const columns: ColumnsType<TenantItem> = [
    { title: "租户名称", dataIndex: "name" },
    { title: "租户代码", dataIndex: "code", render: (code: string) => <Typography.Text code>{code}</Typography.Text> },
    { title: "状态", dataIndex: "status", render: (status: string) => <Tag color={status === "active" ? "green" : "default"}>{status === "active" ? "启用" : "停用"}</Tag> },
    { title: "创建时间", dataIndex: "created_at", render: (value: string) => value.replace("T", " ").slice(0, 19) },
    { title: "操作", render: (_, row) => row.code === "default" ? <Typography.Text type="secondary">平台管理租户</Typography.Text> : <Space>
      <Typography.Link href={`/login?tenant=${encodeURIComponent(row.code)}`} target="_blank" rel="noopener noreferrer">登录链接</Typography.Link>
      <Popconfirm title={row.status === "active" ? `停用“${row.name}”？` : `启用“${row.name}”？`} description={row.status === "active" ? "该租户所有账号将无法登录，业务数据保留。" : "客户需要重新登录。"} onConfirm={() => changeStatus.mutateAsync({ id: row.id, status: row.status === "active" ? "disabled" : "active" })}>
        <Button type="link" danger={row.status === "active"} loading={changeStatus.isPending && changeStatus.variables?.id === row.id}>{row.status === "active" ? "停用" : "启用"}</Button>
      </Popconfirm>
    </Space> },
  ];
  function closeCredentials() { setCredentials(null); create.reset(); }
  return <Card title={<Typography.Title level={4} style={{ margin: 0 }}>租户管理</Typography.Title>} extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => { create.reset(); form.resetFields(); setOpen(true); }}>新增租户</Button>}>
    <Typography.Paragraph type="secondary">每个租户拥有独立账号和业务数据。开户后把登录链接、租户代码与初始账号私密交付给客户。</Typography.Paragraph>
    {list.isError && <Alert type="error" showIcon message={extractErrorMessage(list.error, "租户列表加载失败")} action={<Button onClick={() => void list.refetch()}>重试</Button>} style={{ marginBottom: 16 }} />}
    <Table rowKey="id" columns={columns} dataSource={list.data?.items ?? []} loading={list.isLoading} pagination={{ current: page, pageSize: 20, total: list.data?.meta.total ?? 0, onChange: setPage, showTotal: (total) => `共 ${total} 个租户` }} />
    <Modal title="新增租户" open={open} onCancel={() => { if (!create.isPending) { setOpen(false); create.reset(); } }} onOk={() => form.submit()} okText="创建租户" cancelText="取消" confirmLoading={create.isPending} destroyOnHidden>
      <Form form={form} layout="vertical" initialValues={{ admin_username: "admin" }} onFinish={(values) => create.mutate(values)} disabled={create.isPending}>
        <Form.Item name="name" label="租户名称" rules={[{ required: true, whitespace: true, max: 128, message: "请输入租户名称（最多128字）" }]}><Input placeholder="例如：客户 A" /></Form.Item>
        <Form.Item name="code" label="租户代码" extra="登录时使用，创建后不能在页面修改。" normalize={(value: string) => value.trim()} rules={[{ required: true, max: 64, pattern: /^[a-zA-Z0-9_-]+$/, message: "请输入1-64位字母、数字、下划线或短横线" }]}><Input placeholder="例如：customer-a" /></Form.Item>
        <Form.Item name="admin_username" label="初始管理员账号" normalize={(value: string) => value.trim()} rules={[{ required: true, min: 3, max: 64, pattern: /^[a-zA-Z0-9_.-]+$/, message: "请输入3-64位字母、数字、下划线、点或短横线" }]}><Input /></Form.Item>
        {create.isError && <Alert type="error" showIcon message={extractErrorMessage(create.error, "开户失败，请核对租户代码是否已存在")} />}
      </Form>
    </Modal>
    <Modal title="租户已创建" open={credentials !== null} onCancel={closeCredentials} onOk={closeCredentials} okText="已保存，关闭" cancelButtonProps={{ style: { display: "none" } }} maskClosable={false} destroyOnHidden>
      {credentials && <>
        <Alert type="warning" showIcon message="初始密码仅本次显示，关闭后不能再次查看" description="请私密交付给客户，首次登录必须修改密码。" style={{ marginBottom: 16 }} />
        <Typography.Paragraph>租户代码：<Typography.Text copyable>{credentials.tenant.code}</Typography.Text></Typography.Paragraph>
        <Typography.Paragraph>管理员账号：<Typography.Text copyable>{credentials.admin_username}</Typography.Text></Typography.Paragraph>
        <Typography.Paragraph>初始密码：<Typography.Text copyable code>{credentials.initial_password}</Typography.Text></Typography.Paragraph>
        <Typography.Paragraph>登录链接：<Typography.Text copyable>{`${window.location.origin}/login?tenant=${encodeURIComponent(credentials.tenant.code)}`}</Typography.Text></Typography.Paragraph>
      </>}
    </Modal>
  </Card>;
}
