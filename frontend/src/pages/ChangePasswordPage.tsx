import { Layout } from "antd";
import { ChangePasswordForm } from "@/features/auth/components/ChangePasswordForm";

export function ChangePasswordPage() {
  return (
    <Layout
      style={{
        minHeight: "100vh",
        background: "#f0f2f5",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <ChangePasswordForm />
    </Layout>
  );
}
