import { useEffect, useState } from "react";
import { Alert, Button, DatePicker, Form, Input, Modal, Upload, message } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import type { Dayjs } from "dayjs";
import { resubmitPromotion, uploadBrandComment } from "@/features/promotion/api";
import type { Promotion, PromotionResubmitRequest } from "@/features/promotion/types";
import { disableFutureDate } from "@/features/promotion/listConstants";
import { extractErrorMessage } from "@/services/apiClient";

type Props = {
  /** 要重新提交的推广单；null = 弹窗关着。 */
  target: Promotion | null;
  onClose: () => void;
};

/** 驳回后重新提交（7a-4）。 */
export function ResubmitModal({ target, onClose }: Props) {
  const qc = useQueryClient();
  const [form] = Form.useForm();
  const [brandFile, setBrandFile] = useState<File | null>(null);

  useEffect(() => {
    if (!target) return;
    setBrandFile(null);
    form.resetFields();
    form.setFieldsValue({
      publish_url: target.publish_url ?? undefined,
      actual_publish_date: target.actual_publish_date
        ? dayjs(target.actual_publish_date)
        : undefined,
    });
  }, [target, form]);

  /** 驳回后重新提交（7a-4）。有新截图先传图，再推进状态。 */
  const resubmitMutation = useMutation({
    mutationFn: async ({
      id,
      payload,
      brandFile,
    }: {
      id: string;
      payload: PromotionResubmitRequest;
      brandFile?: File;
    }) => {
      if (brandFile) {
        await uploadBrandComment(id, brandFile);
      }
      return resubmitPromotion(id, payload);
    },
    onSuccess: () => {
      message.success("已重新提交，等主管审核");
      onClose();
      setBrandFile(null);
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  return (
    <Modal
      title={target ? `重新提交 · ${target.internal_code}` : "重新提交"}
      open={!!target}
      onCancel={() => {
        onClose();
        setBrandFile(null);
      }}
      onOk={() => form.submit()}
      confirmLoading={resubmitMutation.isPending}
      okText="重新提交"
      destroyOnHidden
      width={560}
    >
      {target && (
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 16 }}
          message={`上一轮驳回：${target.review_reason_category ?? "未分类"}`}
          description={target.review_reason || "（没有填写驳回说明）"}
        />
      )}
      <Form
        form={form}
        layout="vertical"
        style={{ marginTop: 16 }}
        onFinish={(v: {
          note: string;
          publish_url?: string;
          actual_publish_date?: Dayjs | null;
        }) => {
          if (!target) return;
          // 只带改了的链接 / 日期：不传后端就不动，留空也不会清掉原值
          const payload: PromotionResubmitRequest = { note: v.note.trim() };
          const url = (v.publish_url ?? "").trim();
          if (url && url !== (target.publish_url ?? "")) {
            payload.publish_url = url;
          }
          const publishDate = v.actual_publish_date
            ? v.actual_publish_date.format("YYYY-MM-DD")
            : null;
          if (publishDate && publishDate !== target.actual_publish_date) {
            payload.actual_publish_date = publishDate;
          }
          resubmitMutation.mutate({
            id: target.id,
            payload,
            brandFile: brandFile ?? undefined,
          });
        }}
      >
        <Form.Item
          name="note"
          label="重提说明"
          rules={[
            {
              required: true,
              whitespace: true,
              message: "请写明改了什么，主管再审时会看到",
            },
          ]}
        >
          <Input.TextArea
            rows={3}
            maxLength={2000}
            showCount
            placeholder="如：已让博主补发，链接已更新"
          />
        </Form.Item>
        <Form.Item
          name="publish_url"
          label="发布链接"
          rules={[{ type: "url", message: "请输入合法 URL" }]}
          extra="预填当前链接，改了才会提交"
        >
          <Input placeholder="https://www.xiaohongshu.com/..." />
        </Form.Item>
        <Form.Item
          name="actual_publish_date"
          label="实际发布日期"
          extra="预填当前日期，改了才会提交；不能晚于今天"
        >
          <DatePicker
            style={{ width: "100%" }}
            disabledDate={disableFutureDate}
            allowClear={false}
          />
        </Form.Item>
        <Form.Item
          label="品牌词评论截图（可选）"
          extra="驳回跟截图有关时重新选一张，会覆盖旧图；不选就沿用旧图"
        >
          <Upload
            accept="image/png,image/jpeg,image/webp"
            maxCount={1}
            beforeUpload={(file) => {
              setBrandFile(file as unknown as File);
              return false;
            }}
            onRemove={() => setBrandFile(null)}
          >
            <Button icon={<UploadOutlined />}>重新上传</Button>
          </Upload>
        </Form.Item>
      </Form>
    </Modal>
  );
}
