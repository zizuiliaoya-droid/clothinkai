// 上传结款聊天截图（流程线 R9、8.4）：财务付款后，PR 通知博主并上传聊天截图，提交即记为已结款。
//
// - 上方只读显示实付金额与调整说明（详情结款块，PR 可读不可写，11-17）
// - 截图必传，格式 / 大小先在前端挡一次（后端还会核对文件头）
import { useEffect, useState } from "react";
import { Alert, Button, Descriptions, Modal, Space, Spin, Typography, Upload, message } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import { useMutation, useQuery } from "@tanstack/react-query";
import { getPromotion, promotionDetailKey, uploadSettlementChat } from "@/features/promotion/api";
import { settlementImageError } from "@/features/promotion/settlementFlow";
import type { Promotion } from "@/features/promotion/types";
import { useFlowFeedback } from "./useFlowFeedback";

type Props = {
  target: Promotion | null;
  onClose: () => void;
};

export function SettlementChatModal({ target, onClose }: Props) {
  const feedback = useFlowFeedback();
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const id = target?.id ?? "";
  const { data, isLoading } = useQuery({
    queryKey: promotionDetailKey(id),
    queryFn: () => getPromotion(id),
    enabled: !!target,
  });

  useEffect(() => {
    if (!target) return;
    setFile(null);
    setFileError(null);
  }, [target]);

  const mutation = useMutation({
    mutationFn: (f: File) => uploadSettlementChat(id, f),
    onSuccess: () => {
      message.success("结款聊天截图已上传，推广单已结款");
      feedback.refresh();
      onClose();
    },
    onError: (err) => {
      feedback.handleError(err);
    },
  });

  function submit() {
    if (!file) {
      setFileError("请选择结款聊天截图");
      return;
    }
    const problem = settlementImageError("结款聊天截图", file);
    if (problem) {
      setFileError(problem);
      return;
    }
    mutation.mutate(file);
  }

  const settlement = data?.settlement;

  return (
    <Modal
      title={target ? `传结款截图 · ${target.internal_code}` : "传结款截图"}
      open={!!target}
      onCancel={onClose}
      onOk={submit}
      okText="提交，记为已结款"
      confirmLoading={mutation.isPending}
      destroyOnHidden
      width={520}
    >
      {isLoading || !data ? (
        <Spin />
      ) : (
        <Space direction="vertical" size={12} style={{ width: "100%", marginTop: 8 }}>
          <Descriptions size="small" column={1} bordered>
            <Descriptions.Item label="实付金额">
              {settlement?.payment_amount != null ? `¥${settlement.payment_amount}` : "—"}
            </Descriptions.Item>
            <Descriptions.Item label="调整说明">
              {settlement?.payment_adjust_note || "—"}
            </Descriptions.Item>
          </Descriptions>
          <Typography.Text type="secondary">
            把财务已付款告诉博主后，上传和博主的结款聊天截图；提交即记为已结款，之后满 7 天可录 7 天数据。
          </Typography.Text>
          <Upload
            accept="image/jpeg,image/png,image/webp"
            maxCount={1}
            beforeUpload={(f) => {
              setFile(f);
              setFileError(settlementImageError("结款聊天截图", f));
              return false;
            }}
            onRemove={() => {
              setFile(null);
              setFileError(null);
            }}
            fileList={file ? [{ uid: "settlement-chat", name: file.name }] : []}
          >
            <Button icon={<UploadOutlined />}>选择截图</Button>
          </Upload>
          {fileError && <Alert type="error" showIcon message={fileError} />}
        </Space>
      )}
    </Modal>
  );
}
