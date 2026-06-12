import { useState } from "react";
import {
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import {
  createUser,
  listUsers,
  resetPassword,
  toggleUser,
  unlockUser,
} from "@/features/user/api";
import type { UserCreatePayload, UserListItem } from "@/features/user/api";
import { extractErrorMessage } from "@/services/apiClient";

const ROLE_OPTIONS = [
  "admin",
  "designer",
  "design_assistant",
  "pattern_maker",
  "merchandiser",
  "pr",
  "pr_manager",
  "finance",
  "operator",
];

export function UserListPage() {
  const qc = useQueryClient();
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<UserCreatePayload>();

  const { data, isLoading } = useQuery({
    queryKey: ["users", page],
    queryFn: () => listUsers({ page, page_size: 20 }),
  });

  const createMutation = useMutation({
    mutationFn: (v: UserCreatePayload) => createUser(v),
    onSuccess: (res) => {
      setOpen(false);
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["users"] });
      Modal.success({
        title: "用户已创建",
        content: `临时密码（仅此一次显示）：${res.initial_password}`,
      });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const toggleMutation = useMutation({
    mutationFn: (id: string) => toggleUser(id),
    onSuccess: () => {
      message.success("状态已切换");
      void qc.invalidateQueries({ queryKey: ["users"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const unlockMutation = useMutation({
    mutationFn: (id: string) => unlockUser(id),
    onSuccess: () => {
      message.success("已解锁");
      void qc.invalidateQueries({ queryKey: ["users"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const resetMutation = useMutation({
    mutationFn: (id: string) => resetPassword(id),
    onSuccess: (res) => {
      Modal.success({
        title: "密码已重置",
        content: `新临时密码：${res.initial_password}`,
      });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const columns: ColumnsType<UserListItem> = [
    { title: "用户名", dataIndex: "username", width: 140 },
    { title: "姓名", dataIndex: "display_name", render: (v) => v || "—" },
    { title: "邮箱", dataIndex: "email", render: (v) => v || "—" },
    {
      title: "角色",
      dataIndex: "roles",
      render: (roles: string[]) =>
        roles.length ? roles.map((r) => <Tag key={r}>{r}</Tag>) : "—",
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 90,
      render: (v: string, r) =>
        r.locked_at ? (
          <Tag color="volcano">已锁定</Tag>
        ) : v === "active" ? (
          <Tag color="green">启用</Tag>
        ) : (
          <Tag color="red">禁用</Tag>
        ),
    },
    {
      title: "最后登录",
      dataIndex: "last_login_at",
      width: 170,
      render: (v) => (v ? v.replace("T", " ").slice(0, 19) : "—"),
    },
    {
      title: "操作",
      width: 220,
      render: (_, r) => (
        <Space>
          <Button type="link" size="small" onClick={() => toggleMutation.mutate(r.id)}>
            {r.status === "active" ? "禁用" : "启用"}
          </Button>
          {r.locked_at && (
            <Button type="link" size="small" onClick={() => unlockMutation.mutate(r.id)}>
              解锁
            </Button>
          )}
          <Popconfirm
            title="确定重置该用户密码？"
            onConfirm={() => resetMutation.mutate(r.id)}
          >
            <Button type="link" size="small" danger>
              重置密码
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title={<Typography.Title level={4} style={{ margin: 0 }}>用户管理</Typography.Title>}
      extra={
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            form.resetFields();
            setOpen(true);
          }}
        >
          新建用户
        </Button>
      }
    >
      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        pagination={{
          current: data?.meta.page ?? 1,
          pageSize: data?.meta.page_size ?? 20,
          total: data?.meta.total ?? 0,
          showTotal: (t) => `共 ${t} 人`,
          onChange: (p) => setPage(p),
        }}
      />

      <Modal
        title="新建用户"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={createMutation.isPending}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          style={{ marginTop: 16 }}
          onFinish={(v) => createMutation.mutate(v)}
        >
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: "请输入用户名" }]}
          >
            <Input placeholder="3-64 位字母数字 _ - ." />
          </Form.Item>
          <Form.Item name="display_name" label="姓名">
            <Input placeholder="显示名（可选）" />
          </Form.Item>
          <Form.Item name="email" label="邮箱">
            <Input placeholder="邮箱（可选）" />
          </Form.Item>
          <Form.Item
            name="role_codes"
            label="角色"
            rules={[{ required: true, message: "请选择至少一个角色" }]}
          >
            <Select
              mode="multiple"
              placeholder="选择角色"
              options={ROLE_OPTIONS.map((r) => ({ label: r, value: r }))}
            />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
