import { Layout } from "antd";
import { LoginForm } from "@/features/auth/components/LoginForm";

export function LoginPage() {
  return (
    <Layout style={{ minHeight: "100vh", background: "#f0f2f5" }}>
      <div
        style={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <LoginForm />
      </div>
    </Layout>
  );
}
