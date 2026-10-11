import { useEffect, useState } from "react";
import { Alert, Button, Form, Modal, Space, Spin, message, theme } from "antd";
import { CheckCircleFilled } from "@ant-design/icons";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { FlowAction } from "@/features/flow/FlowAction";
import type { GateMissingItem } from "@/features/flow/keys";
import { missingToFields } from "@/features/flow/missingToFields";
import { ReceiverFields } from "@/features/promotion/components/ReceiverFields";
import {
  RECEIVER_FIELDS,
  RECEIVER_LABEL,
  receiverPatch,
  receiverPhoneError,
  type ReceiverValues,
} from "@/features/promotion/components/receiverForm";
import { useAuthStore } from "@/stores/authStore";
import { confirmNegotiationReceiver, getNegotiation, saveNegotiationReceiver } from "../api";
import {
  RECEIVER_CONFIRM_TEXT,
  promotionSearchPath,
  receiverConfirmBody,
  receiverPrefill,
  receiverProxyNotice,
  type ReceiverPrefill,
} from "../negotiationDialogs";
import type { NegotiationReceiverConfirm, NegotiationReceiverUpdate } from "../types";
import type { NegotiationDialogTarget } from "./ManagerReviewModal";
import { useNegotiationFeedback } from "./useNegotiationFeedback";

type Props = {
  /** 菜单「录收货信息」「确认收货并生成推广单」打开；null = 弹窗关着。 */
  target: NegotiationDialogTarget | null;
  onClose: () => void;
};

/** 缺项 key（★⁴）= 表单字段名。 */
const FIELD_MAP: Partial<Record<string, string>> = Object.fromEntries(RECEIVER_FIELDS.map((f) => [f, f]));

/**
 * 录收货信息（N8 保存、N9 确认并生成推广单，流程线 8.3）。
 * 预填取详情的 receiver_suggestion；谈款 PR 不在时主管 / 代理人 / 管理员代录代确认，顶部写明。
 * 两个按钮按详情的 ui 渲染；财务、运营拿不到这两个动作，打不开这个弹窗。
 */
export function NegotiationReceiverModal({ target, onClose }: Props) {
  const { token } = theme.useToken();
  const navigate = useNavigate();
  const [form] = Form.useForm<ReceiverValues>();
  const { refresh, handleError } = useNegotiationFeedback();
  const userId = useAuthStore((s) => s.user?.id);
  const [prefill, setPrefill] = useState<ReceiverPrefill | null>(null);
  const [rest, setRest] = useState<string | null>(null);

  // receiver_suggestion 只在详情里有
  const detailQuery = useQuery({
    queryKey: ["negotiation", target?.row.id],
    enabled: !!target,
    queryFn: () => getNegotiation(target!.row.id),
    staleTime: 0,
  });
  const row = detailQuery.data ?? target?.row ?? null;
  const ready = !!target && (detailQuery.isSuccess || detailQuery.isError);
  const ui = detailQuery.data?.ui ?? target?.row.ui ?? null;

  useEffect(() => {
    if (!target) {
      setPrefill(null);
      setRest(null);
      return;
    }
    if (!ready || !row) return;
    const p = receiverPrefill(row);
    setPrefill(p);
    form.setFieldsValue(p.values);
    // 详情只在打开时初始化一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, ready]);

  function markMissing(list: GateMissingItem[]) {
    const { fields, rest: unmapped } = missingToFields(list, FIELD_MAP);
    form.setFields(fields as Parameters<typeof form.setFields>[0]);
    setRest(unmapped);
  }

  function onError(err: unknown) {
    const phone = receiverPhoneError(err);
    if (phone) {
      form.setFields([{ name: "receiver_phone", errors: [phone] }]);
      return;
    }
    const gate = handleError(err);
    if (gate) markMissing(gate);
  }

  const saveMutation = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: NegotiationReceiverUpdate }) =>
      saveNegotiationReceiver(id, patch),
    onSuccess: () => {
      message.success("收货信息已保存");
      refresh();
      onClose();
    },
    onError,
  });

  const confirmMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: NegotiationReceiverConfirm }) =>
      confirmNegotiationReceiver(id, body),
    onSuccess: (saved) => {
      const code = saved.promotion_internal_code ?? "";
      message.success(code ? `已生成推广单 ${code}` : "已生成推广单");
      refresh();
      onClose();
      if (!code) return;
      Modal.confirm({
        icon: <CheckCircleFilled style={{ color: token.colorSuccess }} />,
        title: `已生成推广单 ${code}`,
        content: "之后寄样、催发、结款都在推广管理里走。",
        okText: "去推广管理",
        cancelText: "留在谈款页",
        onOk: () => navigate(promotionSearchPath(code)),
      });
    },
    onError,
  });

  function save() {
    if (!target || !prefill || busy) return;
    const patch = receiverPatch(prefill.stored, form.getFieldsValue());
    if (Object.keys(patch).length === 0) {
      message.info("没有改动");
      return;
    }
    saveMutation.mutate({ id: target.row.id, patch });
  }

  function confirm() {
    if (!target || !ready || busy) return;
    const body = receiverConfirmBody(form.getFieldsValue());
    // 先在前端查三项都填了（电话格式交给后端 ★⁴）
    const empty = RECEIVER_FIELDS.filter((f) => !body[f]);
    if (empty.length) {
      markMissing(empty.map((f) => ({ key: f, label: RECEIVER_LABEL[f] })));
      return;
    }
    setRest(null);
    Modal.confirm({
      title: "确认收货信息",
      content: RECEIVER_CONFIRM_TEXT,
      okText: "确认并生成推广单",
      cancelText: "再看看",
      onOk: () => confirmMutation.mutate({ id: target.row.id, body }),
    });
  }

  const proxy = row ? receiverProxyNotice(row, userId) : null;
  const busy = saveMutation.isPending || confirmMutation.isPending;

  return (
    <Modal
      title="录收货信息"
      open={!!target}
      onCancel={onClose}
      destroyOnHidden
      width={560}
      footer={
        <Space wrap style={{ justifyContent: "flex-end" }}>
          <Button onClick={onClose}>取消</Button>
          <FlowAction
            ui={ui}
            actionKey="receiver_save"
            label="保存"
            onClick={save}
            buttonProps={{ loading: saveMutation.isPending }}
          />
          <FlowAction
            ui={ui}
            actionKey="receiver_confirm"
            label="确认并生成推广单"
            onClick={confirm}
            buttonProps={{ type: "primary", loading: confirmMutation.isPending }}
          />
        </Space>
      }
    >
      {proxy && <Alert type="info" showIcon style={{ marginTop: 16 }} message={proxy} />}
      {prefill?.note && <Alert type="warning" showIcon style={{ marginTop: 12 }} message={prefill.note} />}
      {rest && <Alert type="error" showIcon style={{ marginTop: 12 }} message={rest} />}
      <Spin spinning={!ready}>
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <ReceiverFields required={false} />
        </Form>
      </Spin>
    </Modal>
  );
}
