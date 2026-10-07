import { useEffect, useState } from "react";
import { Button, Form, Input, Modal, Select, Space, Spin, Typography, Upload, message } from "antd";
import { DeleteOutlined, UploadOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createStyle,
  getStyle,
  listDictItems,
  removeStyleMainImage,
  updateStyle,
  uploadStyleMainImage,
} from "@/features/product/api";
import type { Gender, Style } from "@/features/product/types";
import {
  compressStyleMainImage,
  formatImageKilobytes,
  type CompressedStyleImage,
} from "@/features/product/imageCompression";
import { extractErrorMessage } from "@/services/apiClient";
import { StyleImageThumbnail } from "@/components/StyleImageThumbnail/StyleImageThumbnail";

const GENDERS: Gender[] = ["女", "男", "中性", "童"];

export interface StyleEditModalProps {
  open: boolean;
  mode: "create" | "edit";
  /** 编辑模式必传：要编辑的款式 ID（弹窗打开时按 ID 取最新数据）。 */
  styleId?: string;
  /** 新建模式可选：预填的货号（商品弹窗里搜不到款式时，把搜索关键词带过来）。 */
  initialCode?: string;
  onClose: () => void;
  /** 保存成功（含主图上传 / 移除）后回调，参数是最终的款式。 */
  onSaved?: (style: Style) => void;
}

interface FormValues {
  style_code: string;
  style_name: string;
  gender?: Gender | null;
  tag_color?: string[];
  remark?: string | null;
}

/**
 * 新建 / 编辑款式（8a-1：款式维护并进商品 / 套装页）。
 *
 * 只维护款式自己的东西：货号（建档后不可改）、款名、性别、颜色明细、备注、主图。
 * 简称、季节、品牌归商品层，类目已下线，这里都不出现。
 */
export function StyleEditModal({
  open,
  mode,
  styleId,
  initialCode,
  onClose,
  onSaved,
}: StyleEditModalProps) {
  const qc = useQueryClient();
  const isEdit = mode === "edit";
  const [form] = Form.useForm<FormValues>();
  const [compressedImage, setCompressedImage] = useState<CompressedStyleImage | null>(null);
  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);
  const [removeExistingImage, setRemoveExistingImage] = useState(false);
  const [isCompressing, setIsCompressing] = useState(false);

  const { data: editing, isLoading: styleLoading } = useQuery({
    queryKey: ["styles", "detail", styleId],
    queryFn: () => getStyle(styleId as string),
    enabled: open && isEdit && !!styleId,
  });
  const { data: colors } = useQuery({
    queryKey: ["dict-items", "color"],
    queryFn: () => listDictItems("color"),
    enabled: open,
  });
  const colorOptions = (colors ?? []).map((c) => ({ label: c.value, value: c.value }));

  // 每次打开都从干净的图片状态开始
  useEffect(() => {
    if (!open) return;
    setCompressedImage(null);
    setImagePreviewUrl(null);
    setRemoveExistingImage(false);
    setIsCompressing(false);
  }, [open, styleId]);

  useEffect(() => {
    return () => {
      if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
    };
  }, [imagePreviewUrl]);

  async function selectMainImage(file: File) {
    setIsCompressing(true);
    try {
      const compressed = await compressStyleMainImage(file);
      setCompressedImage(compressed);
      setImagePreviewUrl(URL.createObjectURL(compressed.file));
      setRemoveExistingImage(false);
      message.success(`主图已压缩至 ${formatImageKilobytes(compressed.compressedBytes)}`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "主图压缩失败");
    } finally {
      setIsCompressing(false);
    }
  }

  const saveMutation = useMutation({
    mutationFn: async (values: FormValues) => {
      // 多选清空后 antd 给 undefined，axios 会整个省掉该字段 →
      // 后端 PATCH 语义视为"未修改"，颜色清不掉。这里显式归一为空数组；性别 / 备注同理归一为 null。
      const common = {
        style_name: values.style_name,
        gender: values.gender ?? null,
        tag_color: values.tag_color ?? [],
        remark: values.remark ?? null,
      };
      let saved: Style;
      if (isEdit && editing) {
        // 款号建档后不可改：编辑时不提交 style_code
        saved = await updateStyle(editing.id, common);
      } else {
        saved = await createStyle({ style_code: values.style_code, ...common });
      }
      if (compressedImage) {
        saved = await uploadStyleMainImage(saved.id, compressedImage.file);
      } else if (editing?.main_image_key && removeExistingImage) {
        await removeStyleMainImage(saved.id);
        saved = {
          ...saved,
          main_image_key: null,
          main_image_url: null,
          image_url: saved.external_image_url,
          image_source: saved.external_image_url ? "external" : null,
        };
      }
      return saved;
    },
    onSuccess: (saved) => {
      message.success(isEdit ? "款式已更新" : "款式已创建");
      void qc.invalidateQueries({ queryKey: ["styles"] });
      void qc.invalidateQueries({ queryKey: ["goods"] });
      void qc.invalidateQueries({ queryKey: ["production"] });
      void qc.invalidateQueries({ queryKey: ["promotions"] });
      onSaved?.(saved);
      onClose();
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const waitingForStyle = isEdit && (styleLoading || !editing);
  // 款式图（8a-2）：新选的图 > 已上传主图 > 聚水潭外部链接；移除上传主图后回落到外部链接
  const displayedMainImageUrl =
    imagePreviewUrl ??
    (removeExistingImage ? editing?.external_image_url : editing?.image_url) ??
    null;
  // 外部链接只经导入维护，这里只能移除新选的图或已上传的主图
  const canRemoveImage =
    Boolean(compressedImage) || (Boolean(editing?.main_image_key) && !removeExistingImage);

  const initialValues: Partial<FormValues> =
    isEdit && editing
      ? {
          style_code: editing.style_code,
          style_name: editing.style_name,
          gender: (editing.gender as Gender | null) ?? undefined,
          tag_color: editing.tag_color ?? [],
          remark: editing.remark ?? undefined,
        }
      : { style_code: initialCode?.trim() || undefined, tag_color: [] };

  return (
    <Modal
      title={isEdit ? `编辑款式${editing ? ` · ${editing.style_code}` : ""}` : "新建款式"}
      open={open}
      onCancel={onClose}
      onOk={() => form.submit()}
      confirmLoading={saveMutation.isPending || isCompressing}
      okButtonProps={{ disabled: isCompressing || waitingForStyle }}
      destroyOnHidden
      width={560}
    >
      {waitingForStyle ? (
        <div style={{ padding: "48px 0", textAlign: "center" }}>
          <Spin />
        </div>
      ) : (
        <Form
          // 换一个款式就重新挂载，initialValues 才会生效
          key={isEdit ? editing?.id : `create-${initialCode ?? ""}`}
          form={form}
          layout="vertical"
          initialValues={initialValues}
          onFinish={(v) => saveMutation.mutate(v)}
          style={{ marginTop: 16 }}
        >
          <Form.Item
            name="style_code"
            label="货号"
            rules={[
              { required: true, message: "请输入货号" },
              // 与后端 StyleCreate.style_code 的格式一致；从商品弹窗带来的关键词可能是款名
              ...(isEdit
                ? []
                : [{ pattern: /^[A-Za-z0-9_-]+$/, message: "货号只能包含字母、数字、下划线与连字符" }]),
            ]}
            tooltip={isEdit ? "货号建档后不可改：聚水潭导入与批量传图都按货号匹配款式" : undefined}
          >
            <Input placeholder="如 A001" disabled={isEdit} />
          </Form.Item>
          <Form.Item
            name="style_name"
            label="款名"
            rules={[{ required: true, message: "请输入款名" }]}
          >
            <Input placeholder="款式名称" />
          </Form.Item>
          <Form.Item label="款式主图">
            <Space align="start" size="middle" wrap>
              <StyleImageThumbnail
                src={displayedMainImageUrl}
                alt={`${editing?.style_code ?? initialCode ?? "款式"} 主图预览`}
                size={88}
                emptyText
              />
              <Space direction="vertical" size={8}>
                <Upload
                  accept="image/jpeg,image/png,image/webp"
                  maxCount={1}
                  showUploadList={false}
                  beforeUpload={(file) => {
                    void selectMainImage(file);
                    return false;
                  }}
                >
                  <Button icon={<UploadOutlined />} loading={isCompressing}>
                    {displayedMainImageUrl ? "替换主图" : "选择主图"}
                  </Button>
                </Upload>
                {canRemoveImage ? (
                  <Button
                    danger
                    type="text"
                    icon={<DeleteOutlined />}
                    disabled={isCompressing}
                    onClick={() => {
                      setCompressedImage(null);
                      setImagePreviewUrl(null);
                      setRemoveExistingImage(Boolean(editing?.main_image_key));
                    }}
                  >
                    移除主图
                  </Button>
                ) : null}
                <Typography.Text type="secondary">
                  JPG、PNG 或 WebP；最长边压缩至 1600px，保存文件严格小于 300KB。
                </Typography.Text>
                {compressedImage ? (
                  <Typography.Text type="success" role="status">
                    已压缩：{formatImageKilobytes(compressedImage.originalBytes)} →{" "}
                    {formatImageKilobytes(compressedImage.compressedBytes)}（
                    {compressedImage.width}×{compressedImage.height}）
                  </Typography.Text>
                ) : null}
                {removeExistingImage ? (
                  <Typography.Text type="warning" role="status">
                    保存后将移除当前主图。
                  </Typography.Text>
                ) : null}
              </Space>
            </Space>
          </Form.Item>
          <Form.Item name="gender" label="适用性别">
            <Select
              allowClear
              placeholder="性别"
              style={{ width: 140 }}
              options={GENDERS.map((g) => ({ label: g, value: g }))}
            />
          </Form.Item>
          <Form.Item
            name="tag_color"
            label="颜色明细"
            extra="该款实际生产的颜色，可多选。颜色值在页面右上角「管理字典」→「颜色」中维护。"
          >
            <Select
              mode="multiple"
              allowClear
              placeholder="选择颜色（可多选）"
              options={colorOptions}
              notFoundContent="暂无颜色，请先在「管理字典」中添加"
            />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} placeholder="备注（可选）" />
          </Form.Item>
        </Form>
      )}
    </Modal>
  );
}
