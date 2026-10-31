import { useReadOnlySupport } from "@/stores/supportStore";
import { useState } from "react";
import { Button, Form, Input, Modal, Space, message } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LINE_EVENTS_ALL } from "@/features/flow/api";
import { extractErrorMessage } from "@/services/apiClient";
import { updatePromotion } from "../api";
import type { Promotion } from "../types";

export function ContentCategoryEditor({ promotion }: { promotion: Promotion }) {
  const readOnly = useReadOnlySupport();
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<{ content_category: string }>();
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (value: string) => updatePromotion(promotion.id, { content_category: value.trim() || null }),
    onSuccess: () => { setOpen(false); message.success("内容分类已保存"); void qc.invalidateQueries({ queryKey: ["promotions"] }); void qc.invalidateQueries({ queryKey: LINE_EVENTS_ALL }); },
    onError: (err) => message.error(extractErrorMessage(err)),
  });
  return <>
    <Space>{promotion.content_category || "—"}{!readOnly && promotion.ui?.fields?.cooperation?.state === "edit" &&
      <Button size="small" type="link" onClick={() => { form.setFieldsValue({ content_category: promotion.content_category ?? "" }); setOpen(true); }}>编辑内容分类</Button>}</Space>
    <Modal title="编辑内容分类" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} okText="保存" confirmLoading={save.isPending} destroyOnHidden>
      <Form form={form} layout="vertical" onFinish={({ content_category }) => save.mutate(content_category ?? "")}>
        <Form.Item name="content_category" label="内容分类" extra="只修改本单，最多8个字；清空后保存可移除分类。"><Input maxLength={8} allowClear placeholder="单篇 / 合集" /></Form.Item>
      </Form>
    </Modal>
  </>;
}
