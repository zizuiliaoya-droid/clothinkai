import { useState } from "react";
import dayjs, { type Dayjs } from "dayjs";
import { Button, Card, DatePicker, Form, Input, InputNumber, Modal, Select, Space, Typography, message } from "antd";
import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FieldBlock } from "@/features/flow/FieldBlock";
import { LINE_EVENTS_ALL } from "@/features/flow/api";
import { PLATFORMS } from "@/features/common/platforms";
import { replacePlatformPosts } from "../api";
import { disableFutureDate } from "../listConstants";
import type { PlatformPostInput, Promotion } from "../types";
import { extractErrorMessage } from "@/services/apiClient";

type Row = Omit<PlatformPostInput, "actual_publish_date"> & { actual_publish_date?: Dayjs | null };

export function PlatformPostsEditor({ promotion }: { promotion: Promotion }) {
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<{ rows: Row[] }>();
  const qc = useQueryClient();
  const watched = Form.useWatch("rows", form) as Row[] | undefined;
  const save = useMutation({
    mutationFn: (rows: PlatformPostInput[]) => replacePlatformPosts(promotion.id, rows),
    onSuccess: () => {
      setOpen(false);
      message.success("其他平台已保存");
      void qc.invalidateQueries({ queryKey: ["promotions"] });
      void qc.invalidateQueries({ queryKey: LINE_EVENTS_ALL });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });
  const beginEdit = () => {
    form.setFieldsValue({ rows: (promotion.platform_posts ?? []).map((p) => ({ ...p, actual_publish_date: p.actual_publish_date ? dayjs(p.actual_publish_date) : null })) });
    setOpen(true);
  };
  return <>
    <FieldBlock ui={promotion.ui} group="platform_posts" title="其他平台" onEdit={beginEdit}>
      {(promotion.platform_posts ?? []).length ? <Space direction="vertical" style={{ width: "100%" }}>
        {promotion.platform_posts?.map((row) => <Card key={row.id} size="small" title={row.platform}>
          <Space wrap><span>发布日期：{row.actual_publish_date || "—"}</span><span>点赞：{row.like_count ?? "—"}</span></Space>
          <div style={{ overflowWrap: "anywhere" }}>{row.publish_url && /^https?:\/\//i.test(row.publish_url)
            ? <Typography.Link href={row.publish_url} target="_blank" rel="noreferrer">{row.publish_url}</Typography.Link>
            : row.publish_url || "未填链接"}</div>
        </Card>)}
      </Space> : <Typography.Text type="secondary">暂无其他平台记录</Typography.Text>}
    </FieldBlock>
    <Modal title="编辑其他平台" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()}
      okText="保存" confirmLoading={save.isPending} width={680} destroyOnHidden>
      <Typography.Paragraph type="secondary">主平台为{promotion.platform}。其他平台仅作记录，不影响结款与报表。</Typography.Paragraph>
      <Form form={form} layout="vertical" onFinish={({ rows }) => save.mutate((rows ?? []).map((r) => ({ platform: r.platform, publish_url: r.publish_url?.trim() || null, actual_publish_date: r.actual_publish_date?.format("YYYY-MM-DD") ?? null, like_count: r.like_count ?? null })))}>
        <Form.List name="rows" rules={[{ validator: async (_, rows: Row[] = []) => {
          const names = rows.map((r) => r.platform);
          if (rows.length > 5 || new Set(names).size !== names.length || names.includes(promotion.platform)) throw new Error("最多5个平台，不能重复或与主平台相同");
        } }]}>
          {(fields, { add, remove }, { errors }) => <Space direction="vertical" style={{ width: "100%" }}>
            {fields.map(({ key, name, ...rest }) => <Card key={key} size="small" title={`平台 ${name + 1}`}
              extra={<Button aria-label={`删除平台${name + 1}`} icon={<DeleteOutlined />} onClick={() => remove(name)} />}>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "0 12px" }}>
                <Form.Item {...rest} name={[name, "platform"]} label="平台" rules={[{ required: true, message: "请选择平台" }]}>
                  <Select options={[...new Set([...PLATFORMS, ...(promotion.platform_posts ?? []).map((p) => p.platform)])]
                    .filter((p) => p !== promotion.platform && !(watched ?? []).some((r, i) => i !== name && r.platform === p))
                    .map((value) => ({ value, label: value }))} />
                </Form.Item>
                <Form.Item {...rest} name={[name, "actual_publish_date"]} label="发布日期"><DatePicker disabledDate={disableFutureDate} style={{ width: "100%" }} /></Form.Item>
                <Form.Item {...rest} name={[name, "like_count"]} label="点赞"><InputNumber min={0} max={2147483647} precision={0} style={{ width: "100%" }} /></Form.Item>
              </div>
              <Form.Item {...rest} name={[name, "publish_url"]} label="发布链接" rules={[{ validator: async (_, value: string | undefined) => {
                if (!value?.trim()) return;
                try { const url = new URL(value.trim()); if (["http:", "https:"].includes(url.protocol)) return; } catch { /* report below */ }
                throw new Error("请输入 http 或 https 链接");
              } }]}><Input maxLength={512} placeholder="https://" /></Form.Item>
            </Card>)}
            <Form.ErrorList errors={errors} />
            <Button aria-label="添加平台" icon={<PlusOutlined />} disabled={fields.length >= 5} onClick={() => add({})}>添加平台</Button>
          </Space>}
        </Form.List>
      </Form>
    </Modal>
  </>;
}
