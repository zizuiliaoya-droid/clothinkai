// 抽屉底部的留言框（流程线设计 6.6、N11）：文字 + 一张图，至少一项；提交后重新拉时间线。
// 输入框下固定提示「收货信息请填在收货栏」（11-21：留言里写地址只提示、不识别）。
import { useId, useState } from "react";
import { Button, Input, Space, Upload, message, theme } from "antd";
import { PictureOutlined } from "@ant-design/icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { lineEventsKey, postLineComment } from "./api";
import { flowErrorOutcome } from "./flowError";
import {
  COMMENT_IMAGE_TYPES,
  COMMENT_MAX_LENGTH,
  COMMENT_RECEIVER_HINT,
  commentError,
  commentImageError,
} from "./timeline";

export function LineCommentBox({ lineId }: { lineId: string }) {
  const { token } = theme.useToken();
  const qc = useQueryClient();
  const inputId = useId();
  const hintId = useId();
  const errorId = useId();
  const [text, setText] = useState("");
  const [image, setImage] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: lineEventsKey(lineId) });

  const mutation = useMutation({
    mutationFn: () => postLineComment(lineId, { text, image }),
    onSuccess: () => {
      message.success("留言已发出");
      setText("");
      setImage(null);
      setError(null);
      refresh();
    },
    onError: (err) => {
      const outcome = flowErrorOutcome(err);
      message.error(outcome.kind === "message" ? outcome.text : "留言没发出去");
      if (outcome.kind === "message" && outcome.refresh) refresh();
    },
  });

  const submit = () => {
    const problem = commentError(text, image);
    setError(problem);
    if (problem) return;
    mutation.mutate();
  };

  return (
    <div>
      <label htmlFor={inputId} style={{ display: "block", marginBottom: 4, fontWeight: 500 }}>
        留言
      </label>
      <Input.TextArea
        id={inputId}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          if (error) setError(null);
        }}
        maxLength={COMMENT_MAX_LENGTH}
        autoSize={{ minRows: 2, maxRows: 6 }}
        placeholder="写点什么，或贴一张图"
        status={error ? "error" : undefined}
        aria-describedby={error ? `${errorId} ${hintId}` : hintId}
        aria-invalid={error ? true : undefined}
      />
      {error && (
        <div id={errorId} role="alert" style={{ color: token.colorError, fontSize: token.fontSizeSM, marginTop: 4 }}>
          {error}
        </div>
      )}
      <div id={hintId} style={{ color: token.colorTextSecondary, fontSize: token.fontSizeSM, marginTop: 4 }}>
        {COMMENT_RECEIVER_HINT}
      </div>
      <Space style={{ marginTop: 8, width: "100%", justifyContent: "space-between" }} wrap>
        <Upload
          accept={COMMENT_IMAGE_TYPES.join(",")}
          maxCount={1}
          fileList={image ? [{ uid: "comment-image", name: image.name, size: image.size, status: "done" }] : []}
          beforeUpload={(file) => {
            const problem = commentImageError(file);
            if (problem) {
              message.error(problem);
              return Upload.LIST_IGNORE;
            }
            setImage(file as unknown as File);
            if (error) setError(null);
            return false;
          }}
          onRemove={() => setImage(null)}
        >
          <Button icon={<PictureOutlined />}>贴图</Button>
        </Upload>
        <Button type="primary" onClick={submit} loading={mutation.isPending}>
          发送
        </Button>
      </Space>
    </div>
  );
}
