import { Alert, Button, Card, Form, Input, Space, message } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { changePassword } from "../api";
import { useAuthStore } from "@/stores/authStore";
import { extractErrorMessage } from "@/services/apiClient";
import type { ChangePasswordRequest } from "@/types";

export function ChangePasswordForm() {
  const navigate = useNavigate();
  const logout = useAuthStore((s) => s.logout);
  const mustChange = useAuthStore((s) => s.mustChangePassword);
  const [loading, setLoading] = useState(false);

  async function onFinish(values: ChangePasswordRequest & { confirm: string }) {
    if (values.new_password !== values.confirm) {
      void message.error("两次输入的新密码不一致");
      return;
    }
    setLoading(true);
    try {
      await changePassword({
        old_password: values.old_password,
        new_password: values.new_password,
      });
      void message.success("密码修改成功，请重新登录");
      logout();
      navigate("/login", { replace: true });
    } catch (err) {
      void message.error(extractErrorMessage(err, "修改密码失败"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card title="修改密码" style={{ width: 480 }}>
      {mustChange && (
        <Alert
          type="warning"
          showIcon
          message="首次登录需修改密码"
          description="请设置一个新密码后重新登录。"
          style={{ marginBottom: 16 }}
        />
      )}
      <Form<ChangePasswordRequest & { confirm: string }>
        layout="vertical"
        onFinish={onFinish}
        autoComplete="off"
        disabled={loading}
      >
        <Form.Item
          label="原密码"
          name="old_password"
          rules={[{ required: true, message: "请输入原密码" }]}
        >
          <Input.Password />
        </Form.Item>
        <Form.Item
          label="新密码"
          name="new_password"
          rules={[
            { required: true, message: "请输入新密码" },
            { min: 10, message: "至少 10 个字符" },
            {
              pattern: /^(?=.*[A-Z])(?=.*[a-z])(?=.*\d).{10,}$/,
              message: "需含大写、小写、数字（≥10 字符）",
            },
          ]}
          extra="≥10 字符 + 大写 + 小写 + 数字"
        >
          <Input.Password />
        </Form.Item>
        <Form.Item
          label="确认新密码"
          name="confirm"
          dependencies={["new_password"]}
          rules={[{ required: true, message: "请再次输入新密码" }]}
        >
          <Input.Password />
        </Form.Item>
        <Form.Item>
          <Space>
            <Button type="primary" htmlType="submit" loading={loading}>
              提交
            </Button>
          </Space>
        </Form.Item>
      </Form>
    </Card>
  );
}
