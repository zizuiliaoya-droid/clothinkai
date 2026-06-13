import { useState } from "react";
import { Button, Modal, Space, Upload, message } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import type { UploadProps } from "antd";
import { useQueryClient } from "@tanstack/react-query";
import { uploadImportFile } from "@/features/import/api";
import { extractErrorMessage } from "@/services/apiClient";

interface Props {
  /** 后端 importer adapter source（如 manual_style_sku / qianniu / wanxiangtai）。 */
  source: string;
  /** 按钮文案，默认「导入 Excel」。 */
  label?: string;
  /** 上传成功后需要刷新的 react-query key（该模块列表）。 */
  invalidateKeys?: (string | number)[][];
  /** 模板列名提示（可选，展示在弹窗里帮助用户对齐表头）。 */
  templateColumns?: string[];
  size?: "small" | "middle" | "large";
}

/**
 * 通用「上传 Excel/CSV 导入」按钮 —— 复用于各业务模块。
 * 内部统一调 POST /api/imports/upload，按 source 路由到对应 adapter，
 * 兼容各平台导出的 .xlsx/.xls/.csv。
 */
export function ImportUploadButton({
  source,
  label = "导入 Excel",
  invalidateKeys = [],
  templateColumns,
  size = "middle",
}: Props) {
  const qc = useQueryClient();
  const [uploading, setUploading] = useState(false);

  const refresh = () => {
    for (const key of invalidateKeys) {
      void qc.invalidateQueries({ queryKey: key });
    }
    void qc.invalidateQueries({ queryKey: ["import-batches"] });
  };

  const uploadProps: UploadProps = {
    beforeUpload: async (file) => {
      setUploading(true);
      try {
        await uploadImportFile(source, file as File);
        message.success("上传成功，已创建导入批次，解析中…");
        refresh();
      } catch (err) {
        message.error(extractErrorMessage(err));
      } finally {
        setUploading(false);
      }
      return false; // 阻止 antd 默认上传
    },
    showUploadList: false,
    accept: ".csv,.xlsx,.xls",
  };

  const showTemplate = () => {
    if (!templateColumns?.length) return;
    Modal.info({
      title: "支持的表头列（兼容各平台导出 Excel/CSV）",
      width: 560,
      content: (
        <div style={{ maxHeight: 320, overflow: "auto" }}>
          <p style={{ color: "#475569", marginBottom: 8 }}>
            上传文件首行需包含以下任一列名（多余列会原样保留）：
          </p>
          <div>{templateColumns.join("、")}</div>
        </div>
      ),
    });
  };

  return (
    <Space>
      <Upload {...uploadProps}>
        <Button icon={<UploadOutlined />} loading={uploading} size={size}>
          {label}
        </Button>
      </Upload>
      {templateColumns?.length ? (
        <Button type="link" size={size} onClick={showTemplate}>
          列说明
        </Button>
      ) : null}
    </Space>
  );
}
