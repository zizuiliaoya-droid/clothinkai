import { Button, Card, Form, Input, message } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { login, getMe } from "../api";
import { useAuthStore } from "@/stores/authStore";
import { extractErrorMessage } from "@/services/apiClient";
import type { LoginRequest } from "@/types";

export function LoginForm() {
  const navigate = useNavigate();
  const setSession = useAuthStore((s) => s.setSession);
  const [loading, setLoading] = useState(false);

  async function onFinish(values: LoginRequest) {
    setLoading(true);
    try {
      const tokens = await login(values);
      // 立即拉取用户信息（包含 roles）
      // 注意：apiClient 已经持有 access_token 之前先 setTokens
      const { setTokens } = await import("@/services/apiClient");
      setTokens(tokens.access_token, tokens.refresh_token);
      const user = await getMe();
      setSession(
        user,
        tokens.access_token,
        tokens.refresh_token,
        tokens.must_change_password
      );
      void message.success("登录成功");
      if (tokens.must_change_password) {
        navigate("/change-password", { replace: true });
      } else {
        navigate("/", { replace: true });
      }
    } catch (err) {
      void message.error(extractErrorMessage(err, "登录失败"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card title="服装电商运营管理系统" style={{ width: 380 }}>
      <Form<LoginRequest>
        layout="vertical"
        onFinish={onFinish}
        autoComplete="off"
        disabled={loading}
      >
        <Form.Item
          label="用户名"
          name="username"
          rules={[{ required: true, message: "请输入用户名" }]}
        >
          <Input autoFocus placeholder="username" />
        </Form.Item>
        <Form.Item
          label="密码"
          name="password"
          rules={[{ required: true, message: "请输入密码" }]}
        >
          <Input.Password placeholder="password" />
        </Form.Item>
        <Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            block
            loading={loading}
          >
            登录
          </Button>
        </Form.Item>
      </Form>
    </Card>
  );
}
