import { Button, Card, Form, Input, message } from "antd";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { login, getMe } from "../api";
import { useAuthStore } from "@/stores/authStore";
import { extractErrorMessage } from "@/services/apiClient";
import type { LoginRequest } from "@/types";
import { isPlatformSession } from "@/features/auth/sessionMode";

export function LoginForm() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const tenantCode = params.get("tenant") ?? "";
  const setSession = useAuthStore((s) => s.setSession);
  const [loading, setLoading] = useState(false);

  async function onFinish(values: LoginRequest) {
    setLoading(true);
    try {
      const tokens = await login({ ...values, tenant_code: values.tenant_code?.trim() || "default" });
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
        navigate(isPlatformSession(user) ? "/platform" : "/", { replace: true });
      }
    } catch (err) {
      void message.error(extractErrorMessage(err, "登录失败"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card title="服装电商运营管理系统" style={{ width: 380, maxWidth: "calc(100vw - 32px)" }}>
      <Form<LoginRequest>
        key={tenantCode}
        initialValues={{ tenant_code: tenantCode }}
        layout="vertical"
        onFinish={onFinish}
        autoComplete="off"
        disabled={loading}
      >
        <Form.Item
          label="租户代码"
          name="tenant_code"
          extra="新客户填写开户时提供的代码；原客户可留空。"
          rules={[{ max: 64 }, { pattern: /^[a-zA-Z0-9_-]*$/, message: "仅支持字母、数字、下划线和短横线" }]}
          normalize={(value: string) => value.trim()}
        >
          <Input placeholder="原客户默认 default" autoComplete="organization" />
        </Form.Item>
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
