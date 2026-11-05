import type { ClipboardEvent, DragEvent } from "react";
import { Button, Typography, Upload, message, theme } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import { useReadOnlySupport } from "@/stores/supportStore";

const IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"];

export function screenshotImageError(file: File): string | null {
  if (!IMAGE_TYPES.includes(file.type)) return "截图仅支持 JPG、PNG、WebP 图片";
  if (file.size > 10 * 1024 * 1024) return "截图不能超过 10MB";
  return null;
}

/** Only handles images in the receiving upload area; ordinary text pastes are untouched. */
export function pastedImages(data: DataTransfer): File[] {
  const files = Array.from(data.files ?? []).filter((file) => file.type.startsWith("image/"));
  if (files.length) return files;
  return Array.from(data.items ?? [])
    .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter((file): file is File => file !== null);
}

type Props = {
  value: File | null;
  onChange: (file: File | null) => void;
  validate?: (file: File) => string | null;
  disabled?: boolean;
  label?: string;
};

/** A local, single-image picker. Saving/uploading remains the caller's existing action. */
export function ScreenshotUpload({ value, onChange, validate = screenshotImageError, disabled = false, label = "选择截图" }: Props) {
  const { token } = theme.useToken();
  const readOnly = useReadOnlySupport();
  const unavailable = disabled || readOnly;

  function select(files: File[]) {
    if (unavailable || !files.length) return;
    if (files.length > 1) message.info("此处只支持一张图片，已取第一张");
    const problem = validate(files[0]);
    if (problem) {
      message.error(problem);
      return;
    }
    onChange(files[0]);
  }

  function paste(event: ClipboardEvent<HTMLDivElement>) {
    if (unavailable) return;
    const files = pastedImages(event.clipboardData);
    if (!files.length) return;
    event.preventDefault();
    event.stopPropagation();
    select(files);
  }

  function drop(event: DragEvent<HTMLDivElement>) {
    if (unavailable || !event.dataTransfer.files.length) return;
    event.preventDefault();
    event.stopPropagation();
    select(Array.from(event.dataTransfer.files));
  }

  if (readOnly) return <Typography.Text type="secondary">排查模式仅可查看附件</Typography.Text>;

  return (
    <div
      role="group"
      aria-label="截图上传区"
      aria-disabled={unavailable}
      tabIndex={unavailable ? -1 : 0}
      onPaste={paste}
      onDragOver={(event) => {
        if (!unavailable && Array.from(event.dataTransfer.types).includes("Files")) event.preventDefault();
      }}
      onDrop={drop}
      onClick={(event) => {
        if (!unavailable && !(event.target as Element).closest("button,input,a")) event.currentTarget.focus();
      }}
      style={{ border: `1px dashed ${token.colorBorder}`, borderRadius: token.borderRadius, padding: 12 }}
    >
      <Upload
        accept={IMAGE_TYPES.join(",")}
        maxCount={1}
        disabled={unavailable}
        fileList={value ? [{ uid: "screenshot", name: value.name, size: value.size, status: "done" }] : []}
        beforeUpload={(file, files) => {
          if (file === files[0]) select(files);
          return Upload.LIST_IGNORE;
        }}
        onRemove={() => { if (!unavailable) onChange(null); }}
      >
        <Button icon={<UploadOutlined />} disabled={unavailable}>{label}</Button>
      </Upload>
      <Typography.Text type="secondary" style={{ display: "block", marginTop: 6, fontSize: token.fontSizeSM }}>
        点击此区域后按 Ctrl+V 粘贴截图，也可将图片拖到这里
      </Typography.Text>
    </div>
  );
}
