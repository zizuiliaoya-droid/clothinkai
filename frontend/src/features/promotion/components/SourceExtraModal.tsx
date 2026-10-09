import { useEffect, useState } from "react";
import { Button, Form, Input, Modal, Select, Space, Typography, Upload, message } from "antd";
import { DeleteOutlined, EyeOutlined, UploadOutlined } from "@ant-design/icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  removePaymentQr,
  updatePromotion,
  uploadPaymentQrFile,
} from "@/features/promotion/api";
import { buildSourceExtraPatch } from "@/features/promotion/sourceExtra";
import type { Promotion } from "@/features/promotion/types";
import { listSkusByStyle } from "@/features/product/api";
import { SOURCE_FIELDS, SOURCE_FIELD_NAMES } from "@/features/promotion/listConstants";
import { extractErrorMessage } from "@/services/apiClient";

type Props = {
  /** 打开弹窗时点的那条推广单；null = 弹窗关着。 */
  target: Promotion | null;
  onClose: () => void;
  canManagePaymentQr: boolean;
};

/** 录入推广信息（source_extra 人工源列 + 博主收款码）。 */
export function SourceExtraModal({ target, onClose, canManagePaymentQr }: Props) {
  const qc = useQueryClient();
  const [extraForm] = Form.useForm();
  // 上传 / 移除收款码后用接口返回的新数据替换展示，表单不动
  const [qrUpdated, setQrUpdated] = useState<Promotion | null>(null);
  const extraTarget = target ? (qrUpdated ?? target) : null;
  // 打开弹窗那一刻的表单初值。保存时只交相对它改过的键（7a-5）。
  // 不能从 extraTarget 现算：上传收款码会换成新数据，拿新数据比旧表单，
  // 别人刚写进去的键会被当成「这边清空了」。
  const [extraInitial, setExtraInitial] = useState<Record<string, unknown>>({});
  const [paymentQrFile, setPaymentQrFile] = useState<File | null>(null);
  const [paymentQrUploading, setPaymentQrUploading] = useState(false);
  // §11：颜色及规格按货号联动——当前推广所属款式的 SKU 颜色+尺码组合
  const [colorSizeOptions, setColorSizeOptions] = useState<
    { label: string; value: string }[]
  >([]);

  useEffect(() => {
    setQrUpdated(null);
    if (!target) return;
    setPaymentQrFile(null);
    const se = (target.source_extra ?? {}) as Record<string, unknown>;
    const initial = Object.fromEntries(SOURCE_FIELD_NAMES.map((f) => [f, se[f] ?? ""]));
    setExtraInitial(initial);
    extraForm.resetFields();
    extraForm.setFieldsValue(initial);
    // §11：按货号(款式)加载该款 SKU 的「颜色 + 尺码」组合作为下拉选项
    setColorSizeOptions([]);
    if (target.style_id) {
      void listSkusByStyle(target.style_id)
        .then((skus) => {
          const seen = new Set<string>();
          const opts: { label: string; value: string }[] = [];
          for (const s of skus) {
            const combo = `${s.color}${s.size ? " " + s.size : ""}`.trim();
            if (combo && !seen.has(combo)) {
              seen.add(combo);
              opts.push({ label: combo, value: combo });
            }
          }
          setColorSizeOptions(opts);
        })
        .catch(() => setColorSizeOptions([]));
    }
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
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function closeExtra() {
    onClose();
    setPaymentQrFile(null);
    extraForm.resetFields();
  }

  async function uploadPaymentQr() {
    if (!extraTarget || !paymentQrFile) return;
    if (!["image/jpeg", "image/png", "image/webp"].includes(paymentQrFile.type)) {
      message.error("收款码仅支持 JPG、PNG、WebP 图片");
      return;
    }
    if (paymentQrFile.size > 10 * 1024 * 1024) {
      message.error("收款码图片不能超过 10MB");
      return;
    }
    setPaymentQrUploading(true);
    try {
      const updated = await uploadPaymentQrFile(extraTarget.id, paymentQrFile);
      setQrUpdated(updated);
      setPaymentQrFile(null);
      void qc.invalidateQueries({ queryKey: ["promotions"] });
      message.success("博主收款码已上传");
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setPaymentQrUploading(false);
    }
  }

  async function deletePaymentQr() {
    if (!extraTarget) return;
    setPaymentQrUploading(true);
    try {
      await removePaymentQr(extraTarget.id);
      setQrUpdated({
        ...extraTarget,
        payment_qr_attachment_id: null,
        payment_qr_signed_url: null,
      });
      setPaymentQrFile(null);
      void qc.invalidateQueries({ queryKey: ["promotions"] });
      message.success("收款码已移除");
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setPaymentQrUploading(false);
    }
  }

  return (
    <Modal
      title="录入推广信息（地址/订单号等）"
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
          if (!extraTarget) return;
          // 只交相对打开弹窗时改过的键；清空的给 null（后端删键），没碰的不带 ——
          // 后端按键合并，表单外的键和仓库刚回填的发货单号都不会被冲掉（7a-5）
          const patch = buildSourceExtraPatch(extraInitial, values, SOURCE_FIELD_NAMES);
          if (Object.keys(patch).length === 0) {
            message.info("没有改动");
            closeExtra();
            return;
          }
          updateExtraMutation.mutate({ id: extraTarget.id, source_extra: patch });
        }}
      >
        {canManagePaymentQr && (
          <>
            <Form.Item label="结款信息（博主收款码）" style={{ marginBottom: 12 }}>
              <Space direction="vertical" size={8} style={{ width: "100%" }}>
                {extraTarget?.payment_qr_signed_url ? (
                  <Typography.Link
                    href={extraTarget.payment_qr_signed_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <EyeOutlined /> 查看当前收款码
                  </Typography.Link>
                ) : (
                  <Typography.Text type="secondary">尚未上传收款码</Typography.Text>
                )}
                <Upload
                  accept="image/jpeg,image/png,image/webp"
                  maxCount={1}
                  beforeUpload={(file) => {
                    setPaymentQrFile(file);
                    return false;
                  }}
                  onRemove={() => setPaymentQrFile(null)}
                  fileList={
                    paymentQrFile
                      ? [{ uid: "payment-qr", name: paymentQrFile.name }]
                      : []
                  }
                >
                  <Button icon={<UploadOutlined />} disabled={paymentQrUploading}>
                    选择图片
                  </Button>
                </Upload>
                <Space wrap>
                  <Button
                    type="primary"
                    icon={<UploadOutlined />}
                    disabled={!paymentQrFile}
                    loading={paymentQrUploading}
                    onClick={() => void uploadPaymentQr()}
                  >
                    {extraTarget?.payment_qr_attachment_id ? "替换收款码" : "上传收款码"}
                  </Button>
                  {extraTarget?.payment_qr_attachment_id && (
                    <Button
                      danger
                      icon={<DeleteOutlined />}
                      disabled={paymentQrUploading}
                      onClick={() =>
                        Modal.confirm({
                          title: "移除收款码？",
                          content: "移除后，财务结款信息中的收款码将不可再查看。",
                          okText: "确认移除",
                          okButtonProps: { danger: true },
                          cancelText: "取消",
                          onOk: deletePaymentQr,
                        })
                      }
                    >
                      移除
                    </Button>
                  )}
                </Space>
                <Typography.Text type="secondary">
                  支持 JPG、PNG、WebP，单张不超过 10MB；文件存储于私有空间。
                </Typography.Text>
              </Space>
            </Form.Item>

            <Form.Item label="结款凭证（财务同步）" style={{ marginBottom: 12 }}>
              {extraTarget?.settlement_payment_proof_signed_url ? (
                <Typography.Link
                  href={extraTarget.settlement_payment_proof_signed_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  <EyeOutlined /> 查看结款凭证
                </Typography.Link>
              ) : (
                <Typography.Text type="secondary">财务尚未上传结款凭证</Typography.Text>
              )}
            </Form.Item>
          </>
        )}

        {SOURCE_FIELDS.map((f) => (
          <Form.Item key={f.name} name={f.name} label={f.name} style={{ marginBottom: 12 }}>
            {f.name === "颜色及规格" ? (
              <Select
                allowClear
                showSearch
                placeholder={
                  colorSizeOptions.length
                    ? "按货号选择颜色+尺码组合"
                    : "该款暂无SKU，可在商品成本表维护后选择"
                }
                options={colorSizeOptions}
                notFoundContent="该货号下暂无颜色/尺码，请先在商品成本表维护"
              />
            ) : f.type === "select" ? (
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
