// 仓库回填 / 改单号弹窗（流程线设计 8.5、3.3 S5 / S6）。
// 顶部只读显示收件三项与颜色尺码方便对单；快递公司选项取接口的 couriers；发货时间默认现在、不能选未来。
import { useEffect } from "react";
import { DatePicker, Descriptions, Form, Input, Modal, Select, Tag, message, theme } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { flowErrorOutcome } from "@/features/flow/flowError";
import { itemSpecLines } from "@/features/promotion/shipDisplay";
import { updateWarehouseWaybill } from "../api";
import {
  fillActionLabel,
  fillInitialValues,
  shippedAtError,
  waybillPayload,
  type WaybillFormValues,
} from "../shipmentForm";
import type { WarehouseShipmentRow } from "../types";

export const WAREHOUSE_QUERY_KEY = ["warehouse-shipments"] as const;

export interface WaybillFillModalProps {
  target: WarehouseShipmentRow | null;
  couriers: readonly string[];
  onClose: () => void;
}

export function WaybillFillModal({ target, couriers, onClose }: WaybillFillModalProps) {
  const { token } = theme.useToken();
  const qc = useQueryClient();
  const [form] = Form.useForm<WaybillFormValues>();
  const refresh = () => void qc.invalidateQueries({ queryKey: WAREHOUSE_QUERY_KEY });

  useEffect(() => {
    if (target) form.setFieldsValue(fillInitialValues(target, dayjs()));
  }, [target, form]);

  const mutation = useMutation({
    mutationFn: (values: WaybillFormValues) => {
      if (!target) throw new Error("没有选中的单");
      return updateWarehouseWaybill(
        target.id,
        waybillPayload(
          { courier: values.courier ?? "", waybill: values.waybill, shipped_at: values.shipped_at },
          { status: target.ship_status, shippedAtTouched: form.isFieldTouched("shipped_at") }
        )
      );
    },
    onSuccess: () => {
      message.success(target?.ship_status === "已发货" ? "快递信息已更新" : "已回填，这单进入已发货");
      refresh();
      onClose();
    },
    onError: (err) => {
      const code = (err as { response?: { data?: { code?: unknown } } }).response?.data?.code;
      const outcome = flowErrorOutcome(err);
      if (outcome.kind === "gate") {
        message.error(`缺：${outcome.missing.map((m) => m.label).join("、")}`);
        return;
      }
      if (code === "SHIPPED_AT_IN_FUTURE") {
        form.setFields([{ name: "shipped_at", errors: [outcome.text] }]);
        return;
      }
      message.error(outcome.text);
      if (outcome.refresh) {
        refresh();
        onClose();
      }
    },
  });

  const spec = target ? itemSpecLines(target) : null;
  const dash = <span style={{ color: token.colorTextSecondary }}>—</span>;

  return (
    <Modal
      title={target ? `${fillActionLabel(target.ship_status)} · ${target.internal_code}` : "回填"}
      open={!!target}
      onCancel={onClose}
      onOk={() => form.submit()}
      okText="保存"
      confirmLoading={mutation.isPending}
      destroyOnHidden
    >
      {target && (
        <Descriptions
          size="small"
          column={1}
          bordered
          style={{ marginBottom: 16 }}
          items={[
            { key: "name", label: "收件人", children: target.receiver_name || dash },
            { key: "phone", label: "电话", children: target.receiver_phone || dash },
            { key: "addr", label: "地址", children: target.receiver_address || dash },
            {
              key: "spec",
              label: "颜色尺码",
              children: spec
                ? spec.lines.map((line, i) => (
                    <div key={i}>
                      {line}
                      {spec.legacy && i === 0 && <Tag style={{ marginInlineStart: 4 }}>旧</Tag>}
                    </div>
                  ))
                : dash,
            },
          ]}
        />
      )}
      <Form<WaybillFormValues> form={form} layout="vertical" onFinish={(v) => mutation.mutate(v)}>
        <Form.Item name="courier" label="快递公司" rules={[{ required: true, message: "请选择快递公司" }]}>
          <Select placeholder="选择快递公司" options={couriers.map((c) => ({ value: c, label: c }))} />
        </Form.Item>
        <Form.Item
          name="waybill"
          label="单号"
          rules={[
            { required: true, whitespace: true, message: "请输入快递单号" },
            { max: 128, message: "单号最多 128 个字符" },
          ]}
        >
          <Input placeholder="快递单号" allowClear maxLength={128} />
        </Form.Item>
        <Form.Item
          name="shipped_at"
          label="发货时间"
          required
          rules={[
            {
              validator: (_, value: WaybillFormValues["shipped_at"]) => {
                const err = shippedAtError(value, dayjs());
                return err ? Promise.reject(new Error(err)) : Promise.resolve();
              },
            },
          ]}
        >
          <DatePicker
            showTime={{ format: "HH:mm" }}
            format="YYYY-MM-DD HH:mm"
            style={{ width: "100%" }}
            disabledDate={(d) => d.isAfter(dayjs(), "day")}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}
