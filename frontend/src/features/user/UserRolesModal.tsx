import { Alert, Form, Modal, Select, message } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { assignUserRoles } from "./api";
import type { UserListItem } from "./api";
import { ROLE_OPTIONS } from "./roles";
import { useAuthStore } from "@/stores/authStore";
import { extractErrorMessage } from "@/services/apiClient";

export function UserRolesModal({ user, viewerId, onClose }: {
  user: UserListItem;
  viewerId: string;
  onClose: () => void;
}) {
  const [form] = Form.useForm<{ role_codes: string[] }>();
  const qc = useQueryClient();
  const platformMember = user.roles.includes("platform_admin");
  const change = useMutation({
    mutationFn: (roles: string[]) => assignUserRoles(user.id, roles),
    onSuccess: async () => {
      onClose();
      if (user.id === viewerId && useAuthStore.getState().user?.id === viewerId) {
        message.success("角色已修改，请重新登录");
        useAuthStore.getState().logout();
        return;
      }
      message.success("角色已修改，该用户需重新登录");
      await qc.invalidateQueries({ queryKey: ["users", viewerId] });
      await qc.invalidateQueries({ queryKey: ["user-permissions", viewerId, user.id] });
    },
  });
  return <Modal
    title={`修改角色 · ${user.display_name || user.username}（${user.username}）`}
    open
    onCancel={onClose}
    onOk={() => form.submit()}
    okText="保存角色"
    cancelText="取消"
    confirmLoading={change.isPending}
    destroyOnHidden
  >
    <Alert type="info" showIcon message="可以同时分配多个角色，权限合并生效" description="保存后该用户需重新登录。个人授权与拒绝设置会保留。" style={{ marginBottom: 16 }} />
    {platformMember && <Alert type="info" message="平台管理员身份由运维维护，此处只调整业务角色" style={{ marginBottom: 16 }} />}
    <Form form={form} layout="vertical" initialValues={{ role_codes: user.roles.filter((role) => role !== "platform_admin") }} onFinish={(values) => change.mutate(platformMember ? [...values.role_codes, "platform_admin"] : values.role_codes)}>
      <Form.Item name="role_codes" label="角色" rules={[{ required: true, type: "array", min: 1, message: "请选择至少一个角色" }]}>
        <Select mode="multiple" options={ROLE_OPTIONS} optionFilterProp="label" placeholder="选择角色" disabled={change.isPending} />
      </Form.Item>
    </Form>
    {change.isError && <Alert type="error" showIcon message={extractErrorMessage(change.error, "保存角色失败")} />}
  </Modal>;
}
