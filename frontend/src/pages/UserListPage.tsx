import { useState } from "react";
import {
  Alert,
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
  getUserManagementCapabilities,
  listUsers,
  resetPassword,
  toggleUser,
  unlockUser,
} from "@/features/user/api";
import type { UserCreatePayload, UserListItem } from "@/features/user/api";
import { extractErrorMessage } from "@/services/apiClient";
import { useAuthStore } from "@/stores/authStore";
import { listRegisteredPermissions } from "@/features/user/permissionsApi";
import { UserPermissionsModal } from "@/features/user/UserPermissionsModal";

import { ROLE_OPTIONS, roleLabel } from "@/features/user/roles";
import { UserRolesModal } from "@/features/user/UserRolesModal";

export function UserListPage() {
  const qc = useQueryClient();
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<UserCreatePayload>();
  const [permissionUser, setPermissionUser] = useState<{ user: UserListItem; viewerId: string } | null>(null);
  const [roleUser, setRoleUser] = useState<{ user: UserListItem; viewerId: string } | null>(null);
  const viewerId = useAuthStore((state) => state.user?.id);
  // 后端实际权限决定入口，支持个人管理授权；不从角色推断。
  const catalog = useQuery({
    queryKey: ["permission-catalog", viewerId],
    queryFn: listRegisteredPermissions,
    enabled: Boolean(viewerId),
    retry: false,
  });
  const capabilities = useQuery({
    queryKey: ["user-management-capabilities", viewerId],
    queryFn: getUserManagementCapabilities,
    enabled: Boolean(viewerId),
    retry: false,
  });
  const canAssignRoles = Boolean(viewerId) && capabilities.data?.can_assign_roles === true;
  const canManagePermissions = Boolean(viewerId) && catalog.isSuccess;
  const catalogForbidden = (catalog.error as { response?: { status?: number } } | null)?.response?.status === 403;

  const { data, isLoading } = useQuery({
    queryKey: ["users", viewerId, page],
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
        roles.length ? roles.map((r) => <Tag key={r}>{roleLabel(r)}</Tag>) : "—",
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
      fixed: "right",
      render: (_, r) => (
        <Space wrap>
          {canAssignRoles && (
            <Button type="link" size="small" onClick={() => viewerId && setRoleUser({ user: r, viewerId })}>
              修改角色
            </Button>
          )}
          {canManagePermissions && (
            <Button type="link" size="small" onClick={() => viewerId && setPermissionUser({ user: r, viewerId })}>
              个人权限
            </Button>
          )}
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
      extra={capabilities.data?.can_create_users && (
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
      )}
    >
      {capabilities.isError && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }} message="角色管理入口加载失败" action={<Button onClick={() => void capabilities.refetch()}>重试</Button>} />
      )}
      {catalog.isError && !catalogForbidden && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message={extractErrorMessage(catalog.error, "权限管理入口加载失败")}
          action={<Button onClick={() => void catalog.refetch()}>重试</Button>}
        />
      )}
      <Table
        rowKey="id"
        scroll={{ x: 1000 }}
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

      {roleUser && viewerId === roleUser.viewerId && canAssignRoles && (
        <UserRolesModal key={`${viewerId}:${roleUser.user.id}`} user={roleUser.user} viewerId={viewerId} onClose={() => setRoleUser(null)} />
      )}

      {permissionUser && viewerId === permissionUser.viewerId && canManagePermissions && (
        <UserPermissionsModal
          key={`${viewerId}:${permissionUser.user.id}`}
          user={permissionUser.user}
          viewerId={viewerId}
          catalog={catalog.data ?? []}
          onClose={() => setPermissionUser(null)}
        />
      )}

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
              options={ROLE_OPTIONS}
              optionFilterProp="label"
            />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
