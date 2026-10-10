import { useEffect, useState } from "react";
import { Button, DatePicker, Form, Input, Modal, Upload, message } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { publishPromotion, uploadBrandComment } from "@/features/promotion/api";
import type { Promotion } from "@/features/promotion/types";
import { disableFutureDate } from "@/features/promotion/listConstants";
import { extractErrorMessage } from "@/services/apiClient";

type Props = {
  open: boolean;
  target: Promotion | null;
  /** 点取消：只关弹窗。 */
  onCancel: () => void;
  /** 发布成功：关弹窗并清掉目标。 */
  onDone: () => void;
};

/** 标记发布：发布链接 + 实际发布日期 + 品牌词评论截图。 */
export function PublishModal({ open, target, onCancel, onDone }: Props) {
  const qc = useQueryClient();
  const [form] = Form.useForm();
  const [brandCommentFile, setBrandCommentFile] = useState<File | null>(null);

  useEffect(() => {
    if (!open) return;
    form.resetFields();
    form.setFieldsValue({ actual_publish_date: dayjs() });
    setBrandCommentFile(null);
  }, [open, form]);

  const publishMutation = useMutation({
    // 截图和发布是两个请求，但 UI 上是一步。先传图再发布 —— 顺序不能反，
    // 后端 publish 会检查截图存在
    mutationFn: async ({
      id,
      publish_url,
      actual_publish_date,
      brandCommentFile,
    }: {
      id: string;
      publish_url: string;
      actual_publish_date: string;
      brandCommentFile?: File;
    }) => {
      if (brandCommentFile) {
        await uploadBrandComment(id, brandCommentFile);
      }
      return publishPromotion(id, { publish_url, actual_publish_date });
    },
    onSuccess: () => {
      message.success("已标记发布");
      onDone();
      setBrandCommentFile(null);
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  return (
    <Modal
      title="标记发布"
      open={open}
      onCancel={onCancel}
      onOk={() => form.submit()}
      confirmLoading={publishMutation.isPending}
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        style={{ marginTop: 16 }}
        onFinish={(v) => {
          if (!target) return;
          // 后端的硬门槛：没有品牌词评论截图 publish 直接 422。
          // 这里先拦一次给出清楚的提示，省掉一次无谓的往返
          if (!target.brand_comment_attachment_id && !brandCommentFile) {
            message.error("请先上传品牌词评论截图");
            return;
          }
          publishMutation.mutate({
            id: target.id,
            publish_url: v.publish_url,
            actual_publish_date: dayjs(v.actual_publish_date).format(
              "YYYY-MM-DD"
            ),
            brandCommentFile: brandCommentFile ?? undefined,
          });
        }}
      >
        <Form.Item
          name="publish_url"
          label="发布链接"
          rules={[
            { required: true, message: "请输入发布链接" },
            { type: "url", message: "请输入合法 URL" },
          ]}
        >
          <Input placeholder="https://www.xiaohongshu.com/..." />
        </Form.Item>
        <Form.Item
          name="actual_publish_date"
          label="实际发布日期"
          rules={[{ required: true, message: "请选择发布日期" }]}
        >
          <DatePicker style={{ width: "100%" }} disabledDate={disableFutureDate} />
        </Form.Item>
        <Form.Item
          label="品牌词评论截图"
          required
          extra={
            target?.brand_comment_attachment_id
              ? "已上传过。重新选择会覆盖旧图。"
              : "提交发布审核必传。截图里要能看到品牌词相关评论。"
          }
        >
          <Upload
            accept="image/png,image/jpeg,image/webp"
            maxCount={1}
            beforeUpload={(file) => {
              setBrandCommentFile(file as unknown as File);
              return false;
            }}
            onRemove={() => setBrandCommentFile(null)}
          >
            <Button icon={<UploadOutlined />}>
              {target?.brand_comment_attachment_id
                ? "重新上传"
                : "选择截图"}
            </Button>
          </Upload>
        </Form.Item>
      </Form>
    </Modal>
  );
}
