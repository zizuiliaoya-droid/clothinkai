import { LINE_EVENTS_ALL } from "@/features/flow/api";
import { useEffect, useState } from "react";
import { Form, Input, Modal, Select, message } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { updatePromotion } from "@/features/promotion/api";
import { buildSourceExtraPatch } from "@/features/promotion/sourceExtra";
import type { Promotion } from "@/features/promotion/types";
import { SOURCE_FIELDS, SOURCE_FIELD_NAMES } from "@/features/promotion/listConstants";
import { extractErrorMessage } from "@/services/apiClient";

type Props = {
  /** 打开弹窗时点的那条推广单；null = 弹窗关着。 */
  target: Promotion | null;
  onClose: () => void;
};

/**
 * 其他信息（source_extra 人工源列：订单号等）。
 * 颜色尺码、收件地址、快递单号已不在这里填（流程线 PR-2）：改走「改颜色尺码」「改收件信息」与仓库回填；
 * 博主收款码移到 PaymentQrModal（流程线 PR-6，8.4）。
 */
export function SourceExtraModal({ target, onClose }: Props) {
  const qc = useQueryClient();
  const [extraForm] = Form.useForm();
  // 打开弹窗那一刻的表单初值。保存时只交相对它改过的键（7a-5）。
  const [extraInitial, setExtraInitial] = useState<Record<string, unknown>>({});

  useEffect(() => {
    if (!target) return;
    const se = (target.source_extra ?? {}) as Record<string, unknown>;
    const initial = Object.fromEntries(SOURCE_FIELD_NAMES.map((f) => [f, se[f] ?? ""]));
    setExtraInitial(initial);
    extraForm.resetFields();
    extraForm.setFieldsValue(initial);
  }, [target, extraForm]);

  const updateExtraMutation = useMutation({
    mutationFn: ({
      id,
      source_extra,
    }: {
      id: string;
      source_extra: Record<string, string | null>;
    }) => updatePromotion(id, { source_extra }),
    onSuccess: () => {
      message.success("信息已保存");
      onClose();
      extraForm.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
      void qc.invalidateQueries({ queryKey: LINE_EVENTS_ALL });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function closeExtra() {
    onClose();
    extraForm.resetFields();
  }

  return (
    <Modal
      title="其他信息（订单号等）"
      open={!!target}
      onCancel={closeExtra}
      onOk={() => extraForm.submit()}
      confirmLoading={updateExtraMutation.isPending}
      destroyOnHidden
      width={560}
    >
      <Form
        form={extraForm}
        layout="vertical"
        style={{ marginTop: 16 }}
        onFinish={(values: Record<string, unknown>) => {
          if (!target) return;
          // 只交相对打开弹窗时改过的键；清空的给 null（后端删键），没碰的不带 ——
          // 后端按键合并，表单外的键（导入写的、已删字段的旧值）都不会被冲掉（7a-5）
          const patch = buildSourceExtraPatch(extraInitial, values, SOURCE_FIELD_NAMES);
          if (Object.keys(patch).length === 0) {
            message.info("没有改动");
            closeExtra();
            return;
          }
          updateExtraMutation.mutate({ id: target.id, source_extra: patch });
        }}
      >
        {SOURCE_FIELDS.map((f) => (
          <Form.Item key={f.name} name={f.name} label={f.name} style={{ marginBottom: 12 }}>
            {f.type === "select" ? (
              <Select
                allowClear
                placeholder={`请选择${f.name}`}
                options={(f.options ?? []).map((o) => ({ label: o, value: o }))}
              />
            ) : (
              <Input placeholder={`请输入${f.name}`} allowClear />
            )}
          </Form.Item>
        ))}
      </Form>
    </Modal>
  );
}
