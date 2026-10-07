import { useEffect } from "react";
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Skeleton,
  Space,
  Switch,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import type { TabsProps } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { NetworkDiagnosticsPanel } from "@/features/security/components/NetworkDiagnosticsPanel";
import {
  getWecomConfig,
  testWecom,
  updateWecomConfig,
} from "@/features/settings/api";
import type { WecomConfigUpdate } from "@/features/settings/api";
import { extractErrorMessage } from "@/services/apiClient";
import { useAuthStore } from "@/stores/authStore";

// 企业微信自建应用配置：原「系统设置」页的全部内容，原样搬进「企业微信」Tab
function WecomSettingsPanel() {
  const qc = useQueryClient();
  const [form] = Form.useForm<WecomConfigUpdate>();

  const { data, isLoading } = useQuery({
    queryKey: ["wecom-config"],
    queryFn: () => getWecomConfig(),
  });

  useEffect(() => {
    if (data) {
      form.setFieldsValue({
        corp_id: data.corp_id,
        agent_id: data.agent_id,
        callback_token: data.callback_token ?? undefined,
        default_sender_userid: data.default_sender_userid ?? undefined,
        is_active: data.is_active,
      });
    }
  }, [data, form]);

  const saveMutation = useMutation({
    mutationFn: (v: WecomConfigUpdate) => updateWecomConfig(v),
    onSuccess: () => {
      message.success("企微配置已保存");
      void qc.invalidateQueries({ queryKey: ["wecom-config"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const testMutation = useMutation({
    mutationFn: () => testWecom(),
    onSuccess: (res) =>
      res.ok
        ? message.success("连接成功")
        : message.error(`连接失败：${res.reason ?? "未知"}`),
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  if (isLoading) return <Skeleton active />;

  return (
    <>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="自建应用配置（corp_id + agent_id + secret）。secret 加密存储，不回显。"
        description={
          data?.secret_configured ? (
            <Tag color="green">secret 已配置</Tag>
          ) : (
            <Tag color="orange">secret 未配置</Tag>
          )
        }
      />
      <Form
        form={form}
        layout="vertical"
        style={{ maxWidth: 520 }}
        initialValues={{ is_active: true }}
        onFinish={(v) => saveMutation.mutate(v)}
      >
        <Form.Item
          name="corp_id"
          label="企业 ID (corp_id)"
          rules={[{ required: true, message: "请输入 corp_id" }]}
        >
          <Input placeholder="ww..." />
        </Form.Item>
        <Form.Item
          name="agent_id"
          label="应用 ID (agent_id)"
          rules={[{ required: true, message: "请输入 agent_id" }]}
        >
          <Input placeholder="1000002" />
        </Form.Item>
        <Form.Item
          name="secret"
          label="应用 Secret"
          rules={[{ required: true, message: "请输入 secret（保存后不回显）" }]}
        >
          <Input.Password placeholder="应用 secret" />
        </Form.Item>
        <Form.Item name="callback_token" label="回调 Token">
          <Input placeholder="可选" />
        </Form.Item>
        <Form.Item name="default_sender_userid" label="默认发送人 userid">
          <Input placeholder="可选" />
        </Form.Item>
        <Form.Item name="is_active" label="启用" valuePropName="checked">
          <Switch checkedChildren="启用" unCheckedChildren="停用" />
        </Form.Item>
        <Space>
          <Button
            type="primary"
            htmlType="submit"
            loading={saveMutation.isPending}
          >
            保存配置
          </Button>
          <Button
            onClick={() => testMutation.mutate()}
            loading={testMutation.isPending}
          >
            测试连接
          </Button>
        </Space>
      </Form>
    </>
  );
}

export function SettingsPage() {
  // 条件与 AppLayout 的 isSystemAdmin 一致。只管显示，后端 403 才是闸门。
  // selector 返回布尔：zustand v5 下返回新数组（如 `?? []`）会被判定为每次都变，无限重渲染
  const isSystemAdmin = useAuthStore(
    (s) =>
      s.user?.roles.some((r) => r === "admin" || r === "platform_admin") ??
      false
  );

  const items: TabsProps["items"] = [
    { key: "wecom", label: "企业微信", children: <WecomSettingsPanel /> },
    // 网络诊断会回显原始转发头（暴露内部网络结构），只给系统管理员
    ...(isSystemAdmin
      ? [
          {
            key: "network",
            label: "网络诊断",
            children: <NetworkDiagnosticsPanel />,
          },
        ]
      : []),
  ];

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          系统设置
        </Typography.Title>
      }
    >
      <Tabs defaultActiveKey="wecom" items={items} />
    </Card>
  );
}
