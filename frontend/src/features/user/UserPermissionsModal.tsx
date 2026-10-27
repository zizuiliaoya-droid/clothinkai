import { Alert, Button, Collapse, Form, Input, List, Modal, Popconfirm, Radio, Select, Space, Spin, Tag, Typography, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UserListItem } from "./api";
import { changeUserPermission, getUserPermissions } from "./permissionsApi";
import type { PermissionAction, RegisteredPermission } from "./permissionsApi";
import { extractErrorMessage } from "@/services/apiClient";

interface Props {
  user: UserListItem;
  viewerId: string;
  catalog: RegisteredPermission[];
  onClose: () => void;
}

interface PermissionForm {
  scope: string;
  action: "grant" | "revoke";
  reason: string;
}

export function UserPermissionsModal({ user, viewerId, catalog, onClose }: Props) {
  const [form] = Form.useForm<PermissionForm>();
  const qc = useQueryClient();
  const queryKey = ["user-permissions", viewerId, user.id];
  const permissions = useQuery({
    queryKey,
    queryFn: () => getUserPermissions(user.id),
    retry: false,
  });
  const change = useMutation({
    mutationFn: (values: { scope: string; action: PermissionAction; reason: string }) =>
      changeUserPermission(user.id, values.action, { scope: values.scope, reason: values.reason.trim() }),
    onSuccess: async (_result, values) => {
      message.success(values.action === "reset" ? "已恢复角色默认" : "个人权限已保存");
      form.resetFields();
      await qc.invalidateQueries({ queryKey });
      // 修改自己时重新确认管理能力，避免保留已失效的入口。
      if (user.id === viewerId) {
        await qc.invalidateQueries({ queryKey: ["permission-catalog", viewerId] });
      }
    },
  });
  const names = new Map(catalog.map((item) => [item.scope, item.name]));
  const overrides = [
    ...(permissions.data?.grants ?? []).map((scope) => ({ scope, effect: "grant" })),
    ...(permissions.data?.revokes ?? []).map((scope) => ({ scope, effect: "revoke" })),
  ].sort((a, b) => a.scope.localeCompare(b.scope));
  const ready = permissions.isSuccess && !permissions.isFetching;

  function permissionLabel(scope: string) {
    return (
      <div style={{ minWidth: 0, overflowWrap: "anywhere" }}>
        <Typography.Text>{names.get(scope) || scope}</Typography.Text>
        {names.has(scope) && names.get(scope) !== scope && <div><Typography.Text type="secondary">{scope}</Typography.Text></div>}
      </div>
    );
  }

  return (
    <Modal
      title={`个人权限 · ${user.display_name || user.username}（${user.username}）`}
      open
      onCancel={onClose}
      footer={<Button onClick={onClose}>关闭</Button>}
      width={720}
      destroyOnHidden
    >
      <Alert
        type="info"
        showIcon
        message="个人设置持续生效，需手动撤销"
        description="主管请假时，请分别授予 negotiation.review:approve 和 negotiation_all:read；交接结束后分别恢复角色默认。不会自动到期。"
        style={{ marginBottom: 16, overflowWrap: "anywhere" }}
      />
      {permissions.isPending && <Spin tip="正在读取个人权限"><div style={{ minHeight: 80 }} /></Spin>}
      {permissions.isError && (
        <Alert
          type="error"
          showIcon
          message={extractErrorMessage(permissions.error, "读取个人权限失败")}
          action={<Button onClick={() => void permissions.refetch()}>重试</Button>}
        />
      )}
      {permissions.isSuccess && (
        <>
          <Typography.Title level={5}>当前个人覆盖</Typography.Title>
          <List
            dataSource={overrides}
            locale={{ emptyText: "暂无个人覆盖，使用角色默认权限" }}
            renderItem={(item) => (
              <List.Item>
                <Space direction="vertical" style={{ width: "100%" }}>
                  {permissionLabel(item.scope)}
                  <Space wrap>
                    <Tag color={item.effect === "grant" ? "green" : "red"}>
                      {item.effect === "grant" ? "个人授予" : "个人拒绝"}
                    </Tag>
                    <Popconfirm
                      title="恢复此权限的角色默认设置？"
                      description="仅删除这项个人覆盖，角色权限不变。"
                      onConfirm={() => change.mutate({ scope: item.scope, action: "reset", reason: "撤销个人覆盖，恢复角色默认" })}
                      okText="确认恢复"
                      cancelText="取消"
                      disabled={!ready || change.isPending}
                    >
                      <Button
                        size="small"
                        disabled={!ready || change.isPending}
                        aria-label={`恢复角色默认 ${item.scope}`}
                      >恢复角色默认</Button>
                    </Popconfirm>
                  </Space>
                </Space>
              </List.Item>
            )}
          />
          <Collapse
            size="small"
            style={{ marginTop: 12, marginBottom: 20 }}
            items={[{
              key: "roles",
              label: `角色默认权限（${permissions.data.role_scopes.length}）`,
              children: <List dataSource={permissions.data.role_scopes} locale={{ emptyText: "角色未授予权限" }} renderItem={(scope) => <List.Item>{permissionLabel(scope)}</List.Item>} />,
            }]}
          />
          <Typography.Title level={5}>新增或修改个人覆盖</Typography.Title>
          <Form
            form={form}
            layout="vertical"
            initialValues={{ action: "grant" }}
            disabled={!ready || change.isPending}
            onFinish={(values: PermissionForm) => change.mutate(values)}
          >
            <Form.Item name="scope" label="权限" rules={[{ required: true, message: "请选择已注册权限" }]}>
              <Select
                showSearch
                optionFilterProp="label"
                placeholder="按中文名称或 scope 搜索"
                options={catalog.map((item) => ({ value: item.scope, label: item.name === item.scope ? item.scope : `${item.name}（${item.scope}）` }))}
                popupMatchSelectWidth
                styles={{ popup: { root: { maxWidth: "calc(100vw - 48px)" } } }}
              />
            </Form.Item>
            <Form.Item name="action" label="个人设置" rules={[{ required: true }]}>
              <Radio.Group options={[{ label: "授予", value: "grant" }, { label: "拒绝", value: "revoke" }]} />
            </Form.Item>
            <Form.Item name="reason" label="原因" rules={[{ required: true, whitespace: true, message: "请填写本次设置的原因" }, { max: 512 }]}>
              <Input.TextArea rows={2} maxLength={512} placeholder="例如：主管请假期间代审，交接结束后人工撤销" />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={change.isPending}>保存个人设置</Button>
          </Form>
        </>
      )}
      {change.isError && <Alert style={{ marginTop: 16 }} type="error" showIcon message={extractErrorMessage(change.error, "保存个人权限失败")} />}
    </Modal>
  );
}
